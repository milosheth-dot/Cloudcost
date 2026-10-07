"""Local-only HTTP dashboard. No cloud SDKs, accounts, or third-party assets."""
import argparse
import csv
import io
import json
import secrets
import sqlite3
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from engine import FIELDS, parse_csv, demo_rows, summarize, simulate, anomalies

ROOT = Path(__file__).resolve().parent

class Store:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS observations (dataset_id TEXT NOT NULL, payload TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM datasets').fetchone():
                db.execute('INSERT INTO datasets(id,name) VALUES (?,?)', ('demo', 'Synthetic demo'))
                db.executemany('INSERT INTO observations VALUES (?,?)', [('demo', json.dumps(r)) for r in demo_rows()])
    def list(self):
        with sqlite3.connect(self.path) as db:
            return [dict(id=r[0], name=r[1], created=r[2]) for r in db.execute('SELECT id,name,created FROM datasets ORDER BY created DESC, rowid DESC')]
    def rows(self, dataset):
        with sqlite3.connect(self.path) as db:
            if not db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset,)).fetchone():
                raise ValueError('Unknown dataset')
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM observations WHERE dataset_id=?', (dataset,))]
    def add(self, name, rows):
        dataset = secrets.token_hex(8)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO datasets(id,name) VALUES (?,?)', (dataset, name[:100]))
            db.executemany('INSERT INTO observations VALUES (?,?)', [(dataset, json.dumps(r)) for r in rows])
        return dataset

class Handler(BaseHTTPRequestHandler):
    def respond(self, body, status=200, mime='application/json'):
        if mime == 'application/json':
            body = json.dumps(body, allow_nan=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)
    def valid_host(self):
        # Reject unexpected Host headers (including DNS rebinding).
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
    def selected_rows(self, options):
        rows = self.server.store.rows(options.get('dataset', 'demo'))
        days = int(options.get('days', 30))
        if days not in [7, 30, 90, 365, 0]:
            raise ValueError('Invalid date range')
        if days and rows:
            latest = max(date.fromisoformat(r['date']) for r in rows)
            cutoff = (latest - timedelta(days=days-1)).isoformat()
            rows = [r for r in rows if r['date'] >= cutoff]
        if options.get('date'):
            target_day = date.fromisoformat(options['date']).isoformat()
            rows = [r for r in rows if r['date'] == target_day]
        provider = options.get('filter_provider', 'ALL')
        return [r for r in rows if provider == 'ALL' or r['provider'] == provider]
    def do_GET(self):
        if not self.valid_host():
            return self.respond({'error': 'Invalid host'}, 403)
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        try:
            if parts.path == '/api/data':
                rows = self.selected_rows(query)
                return self.respond(dict(summary=summarize(rows), anomalies=anomalies(rows), datasets=self.server.store.list()))
            if parts.path == '/api/export':
                out = io.StringIO()
                writer = csv.DictWriter(out, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(self.selected_rows(query))
                return self.respond(out.getvalue(), mime='text/csv; charset=utf-8')
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parts.path in assets:
                file, mime = assets[parts.path]
                return self.respond((ROOT/'static'/file).read_bytes(), mime=mime)
            return self.respond({'error': 'Not found'}, 404)
        except (ValueError, TypeError) as e:
            self.respond({'error': str(e)}, 400)
    def do_POST(self):
        if not self.valid_host():
            return self.respond({'error': 'Invalid host'}, 403)
        origin = self.headers.get('Origin')
        allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if origin and origin not in allowed:
            return self.respond({'error': 'Invalid origin'}, 403)
        if self.headers.get('Content-Type') != 'application/json':
            return self.respond({'error': 'Use application/json'}, 415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10_000_000:
                raise ValueError('Request limit: 10 MB')
            options = json.loads(self.rfile.read(size))
            if not isinstance(options, dict):
                raise ValueError('Expected JSON object')
            if self.path == '/api/import':
                text = options.get('csv')
                if not isinstance(text, str):
                    raise ValueError('CSV text is required')
                name = options.get('name', 'CSV import')
                if not isinstance(name, str) or not name.strip():
                    raise ValueError('Dataset name is required')
                rows = parse_csv(text)
                return self.respond({'dataset': self.server.store.add(name.strip(), rows), 'rows': len(rows)}, 201)
            if self.path == '/api/simulate':
                return self.respond(simulate(self.selected_rows(options), options))
            self.respond({'error': 'Not found'}, 404)
        except (ValueError, TypeError, UnicodeError) as e:
            self.respond({'error': str(e)}, 400)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='EcoPulse local cloud cost and carbon auditor')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', type=Path, default=ROOT/'data'/'ecopulse.sqlite3')
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.store = Store(args.db)
    print(f'EcoPulse → http://127.0.0.1:{server.server_port} (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
