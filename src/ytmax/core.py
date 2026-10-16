"""Download a single YouTube video at max quality, degrading gracefully.

Strategy
--------
1. Probe metadata (no download). Reject playlists / unavailable videos early.
2. Build an ordered ladder of yt-dlp format selectors:
     best video+audio  ->  each available height (desc)  ->  best single file  ->  worst
3. Try each rung until one downloads successfully. First success wins.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

ProgressCB = Callable[[dict], None]


class YtMaxError(Exception):
    """Raised when the video cannot be downloaded at any quality."""


@dataclass(frozen=True)
class Result:
    path: Path
    title: str
    video_id: str
    selector: str          # format selector that worked
    height: Optional[int]
    fps: Optional[float]
    vcodec: Optional[str]
    acodec: Optional[str]
    attempts: int          # rungs tried (1 == best quality worked first time)


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _base_opts(
    out_dir: Path,
    container: str,
    cookies: Optional[str],
    cookies_from_browser: Optional[str],
    proxy: Optional[str],
    progress: Optional[ProgressCB],
) -> dict:
    opts: dict = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(out_dir / "%(title).150B [%(id)s].%(ext)s"),
        "merge_output_format": container,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "overwrites": False,
    }
    if cookies:
        opts["cookiefile"] = cookies
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if proxy:
        opts["proxy"] = proxy
    if progress:
        opts["progress_hooks"] = [progress]
    return opts


def _probe(url: str, opts: dict) -> dict:
    probe_opts = {**opts, "skip_download": True}
    probe_opts.pop("progress_hooks", None)
    try:
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as e:
        raise YtMaxError(f"cannot fetch video info: {_clean(e)}") from e
    if not info:
        raise YtMaxError("no info returned")
    if info.get("_type") == "playlist":
        raise YtMaxError("URL is a playlist; pass a single video URL")
    if info.get("is_live"):
        raise YtMaxError("live streams not supported")
    return info


def _ladder(info: dict, ffmpeg: bool) -> list[str]:
    """Ordered format selectors, best first, deduped."""
    fmts = info.get("formats") or []
    heights = sorted(
        {f["height"] for f in fmts if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    ladder: list[str] = []
    if ffmpeg:
        ladder.append("bv*+ba/b")
        ladder += [f"bv*[height={h}]+ba/b[height={h}]" for h in heights]
    else:  # no merging possible -> only single-file formats
        ladder.append("b")
        ladder += [f"b[height={h}]" for h in heights]
    ladder += ["b", "w"]
    seen: set[str] = set()
    return [s for s in ladder if not (s in seen or seen.add(s))]


def _clean(e: Exception) -> str:
    return str(e).replace("ERROR: ", "").strip().splitlines()[-1]


def _purge_partials(out_dir: Path, video_id: str) -> None:
    for p in out_dir.glob(f"*[[]{video_id}[]]*"):
        if p.suffix in {".part", ".ytdl"} or ".f" in p.stem.rsplit("]", 1)[-1]:
            p.unlink(missing_ok=True)


def download(
    url: str,
    out_dir: str | Path = ".",
    *,
    container: str = "mkv",
    cookies: Optional[str] = None,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    progress: Optional[ProgressCB] = None,
    on_attempt: Optional[Callable[[int, str], None]] = None,
) -> Result:
    """Download ONE video at the best quality that works. Returns Result."""
    if container not in {"mkv", "mp4", "webm"}:
        raise ValueError("container must be mkv|mp4|webm")
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(out, container, cookies, cookies_from_browser, proxy, progress)
    info = _probe(url, opts)
    vid = info["id"]
    ladder = _ladder(info, _has_ffmpeg())

    last_err: Optional[Exception] = None
    for n, selector in enumerate(ladder, 1):
        if on_attempt:
            on_attempt(n, selector)
        try:
            with yt_dlp.YoutubeDL({**opts, "format": selector}) as ydl:
                done = ydl.extract_info(url, download=True)
        except (DownloadError, ExtractorError) as e:
            last_err = e
            _purge_partials(out, vid)
            continue

        rd = (done.get("requested_downloads") or [{}])[0]
        path = Path(rd.get("filepath") or ydl.prepare_filename(done))
        if not path.exists():
            last_err = FileNotFoundError(str(path))
            continue
        return Result(
            path=path,
            title=done.get("title", ""),
            video_id=vid,
            selector=selector,
            height=rd.get("height") or done.get("height"),
            fps=rd.get("fps") or done.get("fps"),
            vcodec=rd.get("vcodec") or done.get("vcodec"),
            acodec=rd.get("acodec") or done.get("acodec"),
            attempts=n,
        )

    raise YtMaxError(
        f"all {len(ladder)} format attempts failed; last error: "
        f"{_clean(last_err) if last_err else 'unknown'}"
    )
