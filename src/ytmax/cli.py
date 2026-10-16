from __future__ import annotations

import argparse
import sys

from .core import YtMaxError, download


def _hook(d: dict) -> None:
    if d.get("status") == "downloading":
        pct = (d.get("_percent_str") or "").strip()
        spd = (d.get("_speed_str") or "").strip()
        eta = (d.get("_eta_str") or "").strip()
        sys.stderr.write(f"\r  {pct:>7}  {spd:>12}  eta {eta:<8}")
        sys.stderr.flush()
    elif d.get("status") == "finished":
        sys.stderr.write("\r" + " " * 50 + "\r")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ytmax", description="Download one YouTube video at max working quality.")
    p.add_argument("url", help="single video URL")
    p.add_argument("-o", "--out", default=".", help="output dir (default: .)")
    p.add_argument("-c", "--container", default="mkv", choices=["mkv", "mp4", "webm"],
                   help="merge container (default: mkv; safest for VP9/AV1)")
    p.add_argument("--cookies", help="Netscape cookies.txt")
    p.add_argument("--cookies-from-browser", metavar="BROWSER", help="e.g. chrome, firefox, edge")
    p.add_argument("--proxy")
    a = p.parse_args(argv)

    def on_attempt(n: int, sel: str) -> None:
        print(f"[try {n}] {sel}", file=sys.stderr)

    try:
        r = download(
            a.url, a.out,
            container=a.container,
            cookies=a.cookies,
            cookies_from_browser=a.cookies_from_browser,
            proxy=a.proxy,
            progress=_hook,
            on_attempt=on_attempt,
        )
    except YtMaxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130

    fps = f"{r.fps:g}fps" if r.fps else "?fps"
    print(f"{r.height or '?'}p {fps} {r.vcodec}+{r.acodec}  ->  {r.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
