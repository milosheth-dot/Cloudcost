"""Validated daily observations, transparent emissions, and scenario math."""
import csv
import io
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

FIELDS = ['date', 'provider', 'service', 'region', 'resource_id', 'cost_usd',
          'energy_kwh', 'intensity_g_per_kwh', 'pue']

def number(value, name, low=0, high=1e12):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be a number') from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return result

def validate(rows):
    if not rows:
        raise ValueError('Provide at least one observation')
    if len(rows) > 50000:
        raise ValueError('Limit: 50,000 rows per import')
    clean, keys = [], set()
    for i, row in enumerate(rows, 2):
        try:
            r = {k: row[k] for k in FIELDS}
            r['date'] = date.fromisoformat(str(r['date'])).isoformat()
            for k in ['provider', 'service', 'region', 'resource_id']:
                r[k] = str(r[k]).strip()
                if not r[k] or len(r[k]) > 200:
                    raise ValueError(f'{k} must contain 1–200 characters')
            r['provider'] = r['provider'].upper()
            if r['provider'] not in ['AWS', 'GCP', 'AZURE']:
                raise ValueError('provider must be AWS, GCP, or AZURE')
            for k in ['cost_usd', 'energy_kwh', 'intensity_g_per_kwh']:
                r[k] = number(r[k], k)
            r['pue'] = number(r['pue'], 'pue', 1, 5)
            key = tuple(r[k] for k in FIELDS[:5])
            if key in keys:
                raise ValueError('duplicate daily resource observation')
            keys.add(key)
            clean.append(r)
        except (ValueError, KeyError) as e:
            raise ValueError(f'CSV row {i}: {e}') from None
    return clean

def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
        raise ValueError('Required columns: ' + ','.join(FIELDS))
    return validate(list(reader))

def carbon(r):
    # Input energy is IT energy; PUE adds facility overhead. Grams -> kilograms.
    return r['energy_kwh'] * r['pue'] * r['intensity_g_per_kwh'] / 1000

def summarize(rows):
    daily, resources, services = {}, {}, {}
    for r in rows:
        kg = carbon(r)
        for mapping, key in [(daily, r['date']),
                             (resources, (r['provider'], r['service'], r['region'], r['resource_id'])),
                             (services, (r['provider'], r['service']))]:
            item = mapping.setdefault(key, {'cost': 0, 'carbon': 0, 'energy': 0})
            item['cost'] += r['cost_usd']
            item['carbon'] += kg
            item['energy'] += r['energy_kwh'] * r['pue']
    timeline = [dict(date=k, **v) for k, v in sorted(daily.items())]
    resource_list = [dict(provider=k[0], service=k[1], region=k[2], resource_id=k[3], **v)
                     for k, v in resources.items()]
    return {'cost': sum(r['cost_usd'] for r in rows),
            'carbon': sum(carbon(r) for r in rows), 'rows': len(rows),
            'days': len(daily), 'timeline': timeline,
            'resources': sorted(resource_list, key=lambda x: x['cost'], reverse=True),
            'services': [dict(provider=k[0], service=k[1], **v) for k, v in services.items()]}

def simulate(rows, options):
    provider = options.get('provider', 'ALL')
    resource = options.get('resource_id', 'ALL')
    if provider not in ['ALL', 'AWS', 'GCP', 'AZURE']:
        raise ValueError('Invalid provider')
    share = number(options.get('share', 100), 'share', 0, 100) / 100
    cost_factor = number(options.get('cost_factor', 1), 'cost_factor', 0, 3)
    energy_factor = number(options.get('energy_factor', 1), 'energy_factor', 0, 3)
    target = options.get('target_intensity')
    target = None if target is None else number(target, 'target_intensity', 0, 2000)
    projected = []
    affected = 0
    for r in rows:
        p = dict(r)
        if (provider == 'ALL' or r['provider'] == provider) and (resource == 'ALL' or r['resource_id'] == resource):
            affected += 1
            p['cost_usd'] *= (1 - share) + share * cost_factor
            # Mix moved and unmoved workloads, allowing intensity and efficiency to interact.
            intensity = r['intensity_g_per_kwh'] if target is None else target
            original_kg = carbon(r)
            new_kg = r['energy_kwh'] * r['pue'] * energy_factor * intensity / 1000
            p['energy_kwh'] *= (1 - share) + share * energy_factor
            mixed_kg = original_kg * (1 - share) + new_kg * share
            denominator = p['energy_kwh'] * p['pue']
            p['intensity_g_per_kwh'] = mixed_kg * 1000 / denominator if denominator else 0
        projected.append(p)
    return {'current': summarize(rows), 'projected': summarize(projected), 'affected_rows': affected}

def anomalies(rows):
    """One-sided rolling median/MAD, using only prior calendar days."""
    totals = summarize(rows)['timeline']
    if not totals:
        return []
    lookup = {d['date']: d for d in totals}
    flags = []
    for item in totals:
        day = date.fromisoformat(item['date'])
        # Missing days are unknown, not free/zero-emission days.
        prior = [lookup[(day - timedelta(days=j)).isoformat()] for j in range(1, 15)
                 if (day - timedelta(days=j)).isoformat() in lookup]
        if len(prior) < 7:
            continue
        for metric in ['cost', 'carbon']:
            values = [p[metric] for p in prior]
            baseline = statistics.median(values)
            mad = statistics.median(abs(v - baseline) for v in values)
            threshold = baseline + max(3 * 1.4826 * mad, baseline * .25, .01)
            if item[metric] > threshold:
                contributors = summarize([r for r in rows if r['date'] == item['date']])['resources']
                flags.append({'date': item['date'], 'metric': metric, 'value': item[metric],
                              'baseline': baseline, 'threshold': threshold,
                              'resources': sorted(contributors, key=lambda r: r[metric], reverse=True)[:5]})
    return flags

def demo_rows():
    """Reproducible synthetic dataset, with one intentional spike."""
    specs = [('AWS', 'EC2', 'us-east-1', 'api-server', 4.8, 2.2, 390),
             ('AWS', 'RDS', 'us-east-1', 'production-db', 6.2, 3.5, 390),
             ('GCP', 'Compute Engine', 'europe-west1', 'worker-pool', 3.8, 2.9, 180),
             ('AZURE', 'Virtual Machines', 'eastus', 'analytics', 5.5, 3.1, 420),
             ('AWS', 'S3', 'us-west-2', 'archive', 1.2, .4, 160)]
    rows = []
    end = date.today()
    for i in range(30):
        for j, (provider, service, region, rid, cost, energy, intensity) in enumerate(specs):
            factor = 1 + .06 * math.sin(i + j)
            if i == 24 and rid == 'analytics':
                factor *= 5
            rows.append(dict(date=(end - timedelta(days=29-i)).isoformat(), provider=provider,
                             service=service, region=region, resource_id=rid,
                             cost_usd=round(cost*factor, 4), energy_kwh=round(energy*factor, 4),
                             intensity_g_per_kwh=intensity, pue=1.2))
    return rows
