"""Nút "🕷️ Crawl asset" ở tab 🖼️ Ảnh sản phẩm.

Shopee chặn tải dữ liệu từ ngoài trình duyệt, nên việc tải do extension "Shopee Save" (thư mục dist,
cài trong Chrome) làm: tool mở link sản phẩm kèm `#aff_crawl` -> extension tự tải ảnh chính / ảnh biến thể /
ảnh mô tả / video / thông tin .md vào  Downloads/aff_crawl/<item_id>_<thời gian>/  + file _done.json, rồi tự
đóng tab. Module này mở link, chờ file _done.json xuất hiện và chép mọi thứ vào đúng ô của sản phẩm trong run.

Thư mục tải của Chrome khác mặc định (~/Downloads) -> đặt SHOPEE_DOWNLOAD_DIR trong .env.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from .product_info import info_path, match_product, parse_product_md
from .product_links import _VIDEO_EXTS, _product_folder

CRAWL_FLAG = "aff_crawl"
SUBFOLDERS = ("main_images", "variant_images", "description_images")
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _env_dir() -> str:
    env = os.environ.get("SHOPEE_DOWNLOAD_DIR", "").strip()
    if not env:
        try:
            from dotenv import dotenv_values
            env = (dotenv_values(Path(__file__).resolve().parent.parent / ".env").get("SHOPEE_DOWNLOAD_DIR") or "").strip()
        except Exception:
            env = ""
    return env


def _chrome_download_dirs() -> list[Path]:
    """Thư mục tải đang cài trong Chrome (đọc file Preferences của mọi profile, profile dùng gần nhất trước)."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Google" / "Chrome" / "User Data"
    prefs = sorted(base.glob("*/Preferences"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    out = []
    for pf in prefs:
        try:
            d = json.loads(pf.read_text(encoding="utf-8", errors="ignore")).get("download", {}).get("default_directory")
        except (OSError, ValueError):
            continue
        if d:
            out.append(Path(d))
    return out


def download_roots() -> list[Path]:
    """Mọi chỗ extension có thể đã tải về: SHOPEE_DOWNLOAD_DIR (.env) > thư mục tải của Chrome > ~/Downloads."""
    roots = ([Path(_env_dir()).expanduser()] if _env_dir() else []) + _chrome_download_dirs() + [
        Path.home() / "Downloads", Path.home() / "OneDrive" / "Downloads"]
    seen, out = set(), []
    for r in roots:
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def download_root() -> Path:
    return download_roots()[0]


def crawl_dir() -> Path:
    for r in download_roots():
        if (r / CRAWL_FLAG).exists():
            return r / CRAWL_FLAG
    return download_root() / CRAWL_FLAG


def item_id_of(link: str) -> Optional[str]:
    m = re.search(r"i\.(\d+)\.(\d+)", link or "") or re.search(r"/product/(\d+)/(\d+)", link or "")
    return m.group(2) if m else None


def crawl_url(link: str) -> str:
    # Gắn cả ?aff_crawl=1 lẫn #aff_crawl: 1 số cách mở trình duyệt trên Windows làm rơi phần #, 1 số lần
    # chuyển hướng của Shopee làm rơi phần ? — extension nhận 1 trong 2 là đủ.
    base = (link or "").split("#")[0]
    return base + ("&" if "?" in base else "?") + CRAWL_FLAG + "=1#" + CRAWL_FLAG


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        try:
            from dotenv import dotenv_values
            val = (dotenv_values(Path(__file__).resolve().parent.parent / ".env").get(name) or "").strip()
        except Exception:
            val = ""
    return val.strip('"')


def chrome_path() -> Optional[Path]:
    """chrome.exe: CHROME_PATH (.env) > chỗ cài mặc định của Chrome trên Windows. Không thấy -> None."""
    cands = [_env("CHROME_PATH")] if _env("CHROME_PATH") else []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA"),
                 r"C:\Program Files", r"C:\Program Files (x86)"):
        if base:
            cands.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    found = next((Path(c) for c in cands if c and Path(c).exists()), None)
    if found is None:
        w = shutil.which("chrome") or shutil.which("google-chrome")
        found = Path(w) if w else None
    return found


_EXT_MARKERS = ("extension_shoppe", "aff-crawl", "Shopee Save")
_SETTINGS = Path(__file__).resolve().parent.parent / "data" / "crawl_settings.json"


def _chrome_user_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Google" / "Chrome" / "User Data"


def _has_extension(prof: Path) -> bool:
    for fname in ("Secure Preferences", "Preferences"):
        f = prof / fname
        try:
            text = f.read_text(encoding="utf-8", errors="ignore") if f.exists() else ""
        except OSError:
            continue
        if any(m.lower() in text.lower() for m in _EXT_MARKERS):
            return True
    return False


def list_profiles() -> list[dict]:
    """Các tài khoản (profile) Chrome trên máy: [{"dir": "Profile 1", "name": "Tên hiển thị", "email": ..., "ext": bool}]
    — tên lấy từ file Local State của Chrome (giống danh sách khi bấm ảnh đại diện góc phải Chrome)."""
    base = _chrome_user_data()
    if not base.exists():
        return []
    try:
        cache = json.loads((base / "Local State").read_text(encoding="utf-8", errors="ignore")).get(
            "profile", {}).get("info_cache", {})
    except (OSError, ValueError):
        cache = {}
    dirs = set(cache) | {p.name for p in base.iterdir() if (p / "Preferences").exists()
                         and (p.name == "Default" or p.name.startswith("Profile "))}
    out = []
    for d in sorted(dirs, key=lambda x: (x != "Default", int(x.split()[-1]) if x.split()[-1].isdigit() else 0)):
        info = cache.get(d, {})
        out.append({"dir": d, "name": info.get("gaia_name") or info.get("name") or d,
                    "email": info.get("user_name") or "", "ext": _has_extension(base / d)})
    return out


def saved_profile() -> str:
    try:
        return json.loads(_SETTINGS.read_text(encoding="utf-8")).get("chrome_profile", "")
    except (OSError, ValueError):
        return ""


def save_profile(profile_dir: str) -> None:
    try:
        data = json.loads(_SETTINGS.read_text(encoding="utf-8")) if _SETTINGS.exists() else {}
    except (OSError, ValueError):
        data = {}
    data["chrome_profile"] = profile_dir
    _SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def current_profile() -> str:
    """Profile dùng để mở crawl: bạn chọn ở tool > CHROME_PROFILE (.env) > profile có extension > "" (Chrome tự chọn)."""
    return saved_profile() or _env("CHROME_PROFILE") or extension_profile() or ""


def extension_profile() -> Optional[str]:
    """Profile Chrome có cài extension Shopee Save — CHỈ khi đúng 1 profile có (nhiều profile cùng có thì không đoán,
    để bạn chọn ở tool)."""
    found = [p["dir"] for p in list_profiles() if p["ext"]]
    return found[0] if len(found) == 1 else None


def open_crawl(link: str) -> bool:
    """Mở link bằng CHROME (nơi cài extension Shopee Save) — KHÔNG dùng trình duyệt mặc định của Windows (có thể là
    Edge). Profile Chrome có extension khác profile mặc định -> đặt CHROME_PROFILE=Profile 1 (hoặc Default) trong .env.
    Không tìm thấy Chrome mới quay về trình duyệt mặc định."""
    url = crawl_url(link)
    chrome = chrome_path()
    if chrome:
        import subprocess
        args = [str(chrome)]
        profile = current_profile()
        if profile:
            args.append(f"--profile-directory={profile}")
        try:
            subprocess.Popen(args + [url], close_fds=True)
            return True
        except OSError as e:
            print(f"[CẢNH BÁO] Không mở được Chrome ({chrome}): {e} — thử trình duyệt mặc định.")
    else:
        print("[CẢNH BÁO] Không tìm thấy chrome.exe — đặt CHROME_PATH trong .env. Đang mở bằng trình duyệt mặc định.")
    try:
        return webbrowser.open(url, new=2)
    except Exception:
        return False


DONE_FILES = ("aff_done.json", "_done.json")


def _done_folders() -> list[tuple[Path, dict]]:
    """Mọi thư mục crawl đã có file báo xong (aff_done.json), trong mọi thư mục tải có thể."""
    out = []
    for root in download_roots():
        folder = root / CRAWL_FLAG
        if not folder.exists():
            continue
        for sub in folder.iterdir():
            done = next((sub / n for n in DONE_FILES if (sub / n).exists()), None)
            if done is None:
                continue
            try:
                out.append((sub, json.loads(done.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    out.sort(key=lambda x: x[1].get("time", 0), reverse=True)
    return out


def pending_folders(since: float) -> list[Path]:
    """Thư mục crawl đã bắt đầu (có ảnh) sau `since` nhưng chưa có file báo xong — để báo tiến độ."""
    out = []
    for root in download_roots():
        folder = root / CRAWL_FLAG
        if folder.exists():
            out += [sub for sub in folder.iterdir() if sub.is_dir() and sub.stat().st_mtime >= since - 5
                    and not any((sub / n).exists() for n in DONE_FILES)]
    return out


def latest_result(link: str, since: float = 0.0) -> Optional[tuple[Path, dict]]:
    """Thư mục tải mới nhất của sản phẩm này (khớp item_id; link rút gọn không có id thì lấy thư mục
    xong SAU thời điểm bấm nút)."""
    iid = item_id_of(link)
    for folder, meta in _done_folders():
        if meta.get("time", 0) / 1000 < since - 5:
            continue
        if iid is None or str(meta.get("item_id")) == iid:
            return folder, meta
    return None


def _settled(folder: Path) -> bool:
    """Chrome đã ghi xong hết file (không còn .crdownload / .tmp)."""
    return not any(p.suffix.lower() in (".crdownload", ".tmp", ".part") for p in folder.rglob("*"))


def wait_result(link: str, since: float, timeout: float = 120.0,
                tick: Optional[Callable[[float], None]] = None) -> Optional[tuple[Path, dict]]:
    end = time.time() + timeout
    while time.time() < end:
        found = latest_result(link, since)
        if found and _settled(found[0]):
            time.sleep(1.0)
            if _settled(found[0]):
                return found
        if tick:
            tick(end - time.time())
        time.sleep(1.0)
    return None


def _belongs(folder: Path, meta: dict, product: dict) -> bool:
    """Thư mục crawl này có đúng của sản phẩm không: so item_id; link rút gọn (không có id) thì so file .md."""
    iid = item_id_of(product.get("affiliate_link", ""))
    if iid:
        return str(meta.get("item_id")) == iid
    md = folder / "product_info.md"
    if not md.exists():
        return False
    return match_product(parse_product_md(md.read_text(encoding="utf-8", errors="ignore")), product) is True


def open_many(products: list[dict], gap: float = 1.2) -> None:
    """Mở ĐỒNG THỜI trang của nhiều sản phẩm (mỗi link 1 tab, cách nhau chút để Chrome kịp nhận)."""
    for p in products:
        open_crawl(p["affiliate_link"])
        time.sleep(gap)


def wait_many(products: list[dict], since: float, timeout: float = 180.0,
              on_found: Optional[Callable[[dict, Path], None]] = None) -> tuple[dict, list[dict]]:
    """Chờ các tab tải xong (tab nào xong trước nhận trước). Trả về ({rank: thư mục}, [sản phẩm quá giờ])."""
    pending = {p["rank"]: p for p in products}
    used, results = set(), {}
    end = time.time() + timeout
    while pending and time.time() < end:
        for folder, meta in _done_folders():
            if folder in used or meta.get("time", 0) / 1000 < since - 5 or not _settled(folder):
                continue
            for rank, p in list(pending.items()):
                if _belongs(folder, meta, p):
                    time.sleep(0.5)
                    used.add(folder)
                    results[rank] = folder
                    pending.pop(rank)
                    if on_found:
                        on_found(p, folder)
                    break
        time.sleep(1.0)
    return results, list(pending.values())


def _files(folder: Path, exts: set) -> list[Path]:
    def key(p: Path):
        return (int(p.stem) if p.stem.isdigit() else 10_000, p.name)
    return sorted((p for p in folder.glob("*") if p.suffix.lower() in exts), key=key) if folder.exists() else []


def import_result(src: Path, product: dict, run_dir: Path) -> dict:
    """Chép kết quả crawl vào product_media/<rank>_<tên>/ — THAY ảnh / video cũ của sản phẩm này."""
    dest = _product_folder(product, Path(run_dir))
    stats = {"main_images": 0, "variant_images": 0, "description_images": 0, "video": 0, "info": "không có"}
    for sub in SUBFOLDERS:
        (dest / sub).mkdir(parents=True, exist_ok=True)
        files = _files(src / sub, _IMG_EXTS)
        if not files:
            continue
        for old in _files(dest / sub, _IMG_EXTS):
            old.unlink()
        for i, f in enumerate(files, 1):
            shutil.copy2(f, dest / sub / f"{i:02d}{f.suffix.lower()}")
        stats[sub] = len(files)
    video = src / "video.mp4"
    if video.exists() and video.stat().st_size > 0:
        for old in dest.glob("*"):
            if old.is_file() and old.suffix.lower() in _VIDEO_EXTS:
                old.unlink()
        shutil.copy2(video, dest / "video.mp4")
        stats["video"] = 1
    md = src / "product_info.md"
    if not md.exists():
        md = next(iter(sorted(src.glob("*.md"))), None)
    if md and md.exists():
        text = md.read_text(encoding="utf-8", errors="ignore")
        ok = match_product(parse_product_md(text), product)
        if ok is False:
            stats["info"] = "⚠️ khác sản phẩm — KHÔNG lưu"
        else:
            path = info_path(product, Path(run_dir))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            stats["info"] = "đã lưu"
    return stats


def describe(stats: dict) -> str:
    return (f"{stats['main_images']} ảnh chính · {stats['variant_images']} ảnh biến thể · "
            f"{stats['description_images']} ảnh mô tả · {'có' if stats['video'] else 'không có'} video · "
            f"thông tin .md: {stats['info']}")
