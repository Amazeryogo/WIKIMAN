"""Run with: python -m wikiman [--host 0.0.0.0] [--port 5000]"""
import argparse

from . import create_app


def main():
    ap = argparse.ArgumentParser(prog="wikiman", description="Run the WIKIMAN server.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    app = create_app()
    try:
        from waitress import serve
    except ImportError:
        app.run(host=args.host, port=args.port, debug=args.debug)
    else:
        if args.debug:
            app.run(host=args.host, port=args.port, debug=True)
        else:
            print(f"WIKIMAN on http://{args.host}:{args.port}")
            serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
