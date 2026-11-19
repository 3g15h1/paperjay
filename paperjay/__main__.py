"""`paperjay` / `python -m paperjay`.

    paperjay [serve]   run the web app (HOST, default 0.0.0.0; PORT, default 8000; HTTPS_PORT, default 8443)
    paperjay migrate   upgrade the database now (serve does this too), backing it up first
    paperjay check     verify every stored file against its checksum; exit code 1 if anything is wrong
    paperjay export DIR              copy everything to a plain folder (again: only what changed)
    paperjay import-paperless URL    import from Paperless-ngx (token from --token-file or PAPERLESS_TOKEN)
    paperjay import-export DIR       import a PaperJay export folder (e.g. to restore a backup)
    paperjay add-admin NAME          create an admin account (asks for the password), e.g. if locked out
    paperjay reset-2fa NAME          turn off two-step sign-in for an account whose phone is lost
    paperjay backup                  copy the database to DATA_DIR/backups now (the updater does this first)
    paperjay fetch-models            download the built-in search model to DATA_DIR/models (install/update do this)
"""
import argparse
import json
import logging
import os
import sys

from . import __version__


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="paperjay", description="File your documents, then ask them questions.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="run the web app (default)")
    s.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    s.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    sub.add_parser("migrate", help="upgrade the database schema")
    c = sub.add_parser("check", help="verify stored files and the database")
    c.add_argument("--quick", action="store_true", help="only check that files exist, don't re-hash them")
    e = sub.add_parser("export", help="copy everything to a plain folder")
    e.add_argument("folder")
    e.add_argument("--prune", action="store_true", help="delete files of documents that no longer exist")
    e.add_argument("--no-archive", action="store_true", help="leave out the searchable PDFs")
    ip = sub.add_parser("import-paperless", help="import from Paperless-ngx")
    ip.add_argument("url")
    ip.add_argument("--token-file", help="file holding the Paperless API token (else $PAPERLESS_TOKEN)")
    ip.add_argument("--dry-run", action="store_true", help="only show what would be imported")
    ie = sub.add_parser("import-export", help="import a PaperJay export folder")
    ie.add_argument("folder")
    sub.add_parser("backup", help="copy the database to DATA_DIR/backups")
    sub.add_parser("fetch-models", help="download the built-in search model")
    aa = sub.add_parser("add-admin", help="create an admin account")
    aa.add_argument("username")
    r2 = sub.add_parser("reset-2fa", help="turn off two-step sign-in for an account (lost phone)")
    r2.add_argument("username")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .config import Config
    config = Config.from_env()

    if a.command == "migrate":
        from .db import migrate
        config.data_dir.mkdir(parents=True, exist_ok=True)
        print(f"database is at schema v{migrate(config.db_path, config.backup_dir)}")
    elif a.command == "check":
        from . import check
        from .db import connect, migrate
        from .store import FileStore
        migrate(config.db_path, config.backup_dir)
        con = connect(config.db_path)
        report = check.run(con, FileStore(config.media_dir), verify_hashes=not a.quick)
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["ok"] else 1)
    elif a.command == "backup":
        import time
        from .db import backup, connect, migrate
        migrate(config.db_path, config.backup_dir)
        dest = config.backup_dir / f"paperjay-v{__version__}-{time.strftime('%Y%m%d-%H%M%S')}-manual.db"
        con = connect(config.db_path)
        print(backup(con, dest))
    elif a.command == "fetch-models":
        from . import embedder
        for noisy in ("httpx", "huggingface_hub"):  # one line per downloaded file otherwise
            logging.getLogger(noisy).setLevel(logging.WARNING)
        try:
            print(f"built-in search model ready in {embedder.fetch()}")
        except embedder.Unavailable as e:
            print(f"warning: {e}. Search by meaning will retry later.", file=sys.stderr)
            sys.exit(1)
    elif a.command == "reset-2fa":
        from . import auth
        from .db import connect, migrate
        migrate(config.db_path, config.backup_dir)
        con = connect(config.db_path)
        row = con.execute("SELECT id FROM users WHERE username = ?", (a.username,)).fetchone()
        if not row:
            sys.exit(f"no account called {a.username!r}")
        auth.stop_two_step(con, row[0])
        print(f"two-step sign-in is off for {a.username!r}; they can sign in with their password and turn it on again")
    elif a.command in ("export", "import-paperless", "import-export", "add-admin"):
        from . import importer
        from .db import connect, migrate
        from .store import FileStore
        config.data_dir.mkdir(parents=True, exist_ok=True)
        migrate(config.db_path, config.backup_dir)
        store = FileStore(config.media_dir)
        con = connect(config.db_path)
        if a.command == "export":
            from .export import export
            print(json.dumps(export(con, store, a.folder, prune=a.prune, archives=not a.no_archive), indent=2))
        elif a.command == "import-paperless":
            from pathlib import Path
            token = Path(a.token_file).read_text().strip() if a.token_file else os.environ.get("PAPERLESS_TOKEN", "")
            if not token:
                sys.exit("give the token with --token-file or PAPERLESS_TOKEN")
            if a.dry_run:
                print(json.dumps(importer.check_paperless(con, a.url, token), indent=2))
            else:
                print(json.dumps(importer.import_paperless(config.db_path, store, a.url, token), indent=2))
                print("Start (or restart) `paperjay serve` to read facts with the AI and OCR anything without text.")
        elif a.command == "import-export":
            print(json.dumps(importer.import_export(config.db_path, store, a.folder), indent=2))
        else:
            import getpass
            from . import auth
            pw = getpass.getpass("Password for the new admin: ")
            try:
                auth.create_user(con, a.username, pw, "admin")
            except auth.AuthError as err:
                sys.exit(str(err))
            print(f"admin {a.username!r} created")
    else:
        serve(config, getattr(a, "host", os.environ.get("HOST", "0.0.0.0")),
              getattr(a, "port", int(os.environ.get("PORT", "8000"))),
              getattr(a, "log_level", os.environ.get("LOG_LEVEL", "info")))


def serve(config, host: str, port: int, log_level: str) -> None:
    """The web app on PORT (http) and, unless HTTPS_PORT is off, on HTTPS_PORT with PaperJay's own certificate."""
    import asyncio
    import signal

    import uvicorn
    from .app import create_app

    app = create_app(config)
    common = dict(host=host, log_level=log_level, server_header=False, proxy_headers=True, timeout_graceful_shutdown=5)
    if not config.https_port:
        uvicorn.run(app, port=port, **common)
        return

    class Server(uvicorn.Server):
        def capture_signals(self):  # both servers stop together on one signal (handled below)
            import contextlib
            return contextlib.nullcontext()

    tls = app.state.https
    cert, key = tls.ensure()
    plain = Server(uvicorn.Config(app, port=port, **common))
    secure_cfg = uvicorn.Config(app, port=config.https_port, lifespan="off", ssl_certfile=str(cert),
                                ssl_keyfile=str(key), **common)
    secure_cfg.load()
    # a renewed certificate (or one with a newly learned address) is used for new connections straight away
    tls.on_change.append(lambda: secure_cfg.ssl.load_cert_chain(str(tls.crt), str(tls.key)))
    secure = Server(secure_cfg)

    async def main():
        loop = asyncio.get_running_loop()
        servers = [plain, secure]
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
        plain_task = asyncio.create_task(plain.serve())
        while not plain.started and not plain_task.done():   # the app has started (lifespan) before https opens
            await asyncio.sleep(0.05)
        async def serve_secure():
            try:
                await secure.serve()
            except (SystemExit, OSError):   # e.g. the port is taken: uvicorn exits, but http carries on
                pass
            if not plain.should_exit:
                logging.getLogger("paperjay").error(
                    "HTTPS couldn't start on port %d (see above); PaperJay runs on http only, without the live "
                    "camera scanner. Free the port, or set HTTPS_PORT to another port or off.", config.https_port)
                await plain_task

        secure_task = asyncio.create_task(serve_secure())
        await asyncio.wait([plain_task, secure_task], return_when=asyncio.FIRST_COMPLETED)
        for s in servers:
            s.should_exit = True
        await asyncio.gather(plain_task, secure_task, return_exceptions=True)
        if plain_task.done() and plain_task.exception():
            raise plain_task.exception()

    logging.getLogger("paperjay").info("HTTPS on port %d (for the camera scanner); http on port %d",
                                       config.https_port, port)
    asyncio.run(main())


if __name__ == "__main__":
    main()
