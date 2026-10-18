"""Project-only PostgreSQL cluster; never touches the Windows PostgreSQL service."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, **kwargs)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['start', 'stop', 'status'], nargs='?', default='start')
    p.add_argument('--bin', default=os.environ.get('POSTGRES_BIN', r'C:\Program Files\PostgreSQL\18\bin'))
    args = p.parse_args()
    bindir, cluster = Path(args.bin), RUNTIME / 'postgres'
    exe = '.exe' if os.name == 'nt' else ''
    pgctl = bindir / ('pg_ctl' + exe)
    if not pgctl.exists():
        raise SystemExit('PostgreSQL binaries missing. Set POSTGRES_BIN or use docker compose up --build.')
    RUNTIME.mkdir(exist_ok=True)
    if args.action != 'start':
        run([pgctl, '-D', cluster, args.action] + (['-m', 'fast'] if args.action == 'stop' else []))
        return
    envfile = ROOT / '.env'
    if not envfile.exists():
        password = secrets.token_urlsafe(32)
        template = (ROOT / '.env.example').read_text(encoding='utf-8')
        template = template.replace('CHANGE_ME', password).replace('replace-with-a-random-32-character-token', secrets.token_urlsafe(32)).replace('replace-with-another-random-32-character-token', secrets.token_urlsafe(32))
        envfile.write_text(template + '\nPOSTGRES_PASSWORD=' + password + '\n', encoding='utf-8')
    from dotenv import dotenv_values
    env = dotenv_values(envfile)
    password = env.get('POSTGRES_PASSWORD')
    if not password:
        raise SystemExit('Set POSTGRES_PASSWORD in .env for a dedicated local cluster, or use your configured DB.')
    if not (cluster / 'PG_VERSION').exists():
        password_file = RUNTIME / 'initdb-password.tmp'
        password_file.write_text(password, encoding='utf-8')
        try:
            run([bindir / ('initdb' + exe), '-D', cluster, '-U', 'garden', '--encoding=UTF8', '--locale=C',
                 '--auth=scram-sha-256', '--pwfile', password_file])
        finally:
            password_file.unlink(missing_ok=True)
        with (cluster / 'postgresql.conf').open('a', encoding='utf-8') as f:
            f.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\nmax_connections = 30\nshared_buffers = '64MB'\n")
    result = subprocess.run([str(pgctl), '-D', str(cluster), 'status'], capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        run([pgctl, '-D', cluster, '-l', RUNTIME / 'postgres.log', '-w', 'start'])
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=55432, user='garden', password=password, dbname='postgres', autocommit=True) as con:
        from psycopg import sql
        for database in ['smart_garden', 'smart_garden_test']:
            if not con.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database,)).fetchone():
                con.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    print('PostgreSQL ready: 127.0.0.1:55432. Credentials are in .env (not printed).')


if __name__ == '__main__':
    main()
