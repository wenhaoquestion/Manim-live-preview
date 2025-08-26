import argparse
import os
import sys
import subprocess
import shutil
import time
import webbrowser
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Event, Thread
from typing import Optional, List, Dict

def color(txt, c):
    codes = {
        "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
        "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
        "bold": "\033[1m", "reset": "\033[0m",
    }
    return f"{codes.get(c,'')}{txt}{codes['reset']}"

def info(msg): print(color("[info] ", "cyan") + msg)
def ok(msg): print(color("[ok] ", "green") + msg)
def warn(msg): print(color("[warn] ", "yellow") + msg)
def err(msg): print(color("[error] ", "red") + msg)

@dataclass
class Target:
    src: str
    scene: str
    name: str
    quality: str
    media_dir: Path
    preview_path: Path
    log_path: Path
    lock: Lock
    last_build_t: float = 0.0
    root_dir: Path = None

def check_bin(bin_name: str) -> bool:
    from shutil import which
    return which(bin_name) is not None

def ensure_deps():
    try:
        from livereload import Server  # noqa
    except Exception:
        err("缺少依赖：livereload，请安装： pip install livereload")
        sys.exit(1)
    try:
        import watchdog  # noqa
    except Exception:
        err("缺少依赖：watchdog，请安装： pip install watchdog")
        sys.exit(1)
    if not check_bin("manim"):
        err("未找到 manim 命令。请安装并加入 PATH： pip install manim")
        sys.exit(1)
    if not check_bin("ffmpeg"):
        warn("未检测到 ffmpeg。Manim 导出视频通常需要它，建议安装：https://ffmpeg.org")

def autodetect_scene(src_path: str) -> Optional[str]:
    try:
        spec = importlib.util.spec_from_file_location("manim_src_module", src_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        from manim import Scene
        for name, obj in vars(module).items():
            try:
                if isinstance(obj, type) and issubclass(obj, Scene) and obj is not Scene:
                    return name
            except Exception:
                pass
    except Exception as e:
        warn(f"自动检测场景失败：{e}")
    return None

def run_and_log(cmd: List[str], log_path: Path, verbose: bool) -> int:
    print(color("[cmd] ", "magenta") + " ".join(cmd))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write("\n" + "=" * 80 + "\n")
        lf.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + " ".join(cmd) + "\n")
        lf.write("=" * 80 + "\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for line in proc.stdout:
                print(line, end="")
                lf.write(line)
        except KeyboardInterrupt:
            proc.terminate()
            raise
        return proc.wait()

def tail_log(log_path: Path, n=80):
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        print(color(f"\n--- {log_path.name} 最近日志（tail） ---\n", "blue"))
        for line in lines[-n:]:
            print(line, end="")
        print(color("\n--- 以上 ---\n", "blue"))
    except Exception:
        pass

def find_latest_mp4(root: Path) -> Optional[Path]:
    try:
        mp4s = sorted(root.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        return mp4s[0] if mp4s else None
    except Exception:
        return None

def render_target(t: Target, verbose: bool) -> bool:
    quality_flag = f"-{t.quality}"
    manim_cmd = [
        sys.executable, "-m", "manim",
        t.src, t.scene,
        quality_flag,                         # -ql / -qm / -qh
        "--media_dir", str(t.media_dir),
        "--output_file", t.name,
        "-v", "DEBUG" if verbose else "WARNING",
        "--disable_caching",
    ]
    t0 = time.time()
    code = run_and_log(manim_cmd, t.log_path, verbose)
    dt = time.time() - t0
    if code != 0:
        err(f"[{t.name}] 渲染失败（退出码 {code}，耗时 {dt:.2f}s）。")
        tail_log(t.log_path)
        return False

    latest = find_latest_mp4(t.media_dir)
    if not latest or not latest.exists():
        err(f"[{t.name}] 未找到 MP4 产物，请检查日志：{t.log_path}")
        tail_log(t.log_path)
        return False

    try:
        shutil.copyfile(latest, t.preview_path)
        ok(f"[{t.name}] 预览更新：{t.preview_path} （用时 {dt:.2f}s）")
    except Exception as e:
        err(f"[{t.name}] 复制 MP4 失败：{e}")
        tail_log(t.log_path)
        return False
    return True

DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8" />
  <title>Manim 多场景实时预览</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
  <meta http-equiv="Pragma" content="no-cache"/>
  <meta http-equiv="Expires" content="0"/>
  <style>
    :root {{
      --bg: #0e0f12; --fg: #eaeaea; --card: #15171c; --muted: #9aa0a6; --accent: #ffd166;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin:0; padding:0; background:var(--bg); color:var(--fg); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", Arial, "Microsoft YaHei", sans-serif; }}
    header {{ position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid #2a2d34; padding: 10px 16px; display:flex; align-items: baseline; gap:12px; z-index:10; }}
    h1 {{ margin:0; font-size: 18px; color: var(--accent); }}
    .sub {{ font-size: 13px; color: var(--muted); }}
    main {{ padding: 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }}
    .card {{ background: var(--card); border: 1px solid #262a32; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,.35); overflow:hidden; }}
    .card header {{ display:flex; justify-content:space-between; align-items:center; padding: 10px 12px; border:0; border-bottom: 1px solid #262a32; }}
    .title {{ font-weight: 600; }}
    .meta {{ font-size: 12px; color: var(--muted); }}
    .player {{ display:grid; place-items:center; background:#000; }}
    video {{ width:100%; height: auto; max-height: 70vh; outline:none; display:block; }}
    .hint {{ position: fixed; bottom: 12px; left: 16px; font-size: 12px; color: var(--muted); }}
    code {{ color:#8ecae6; }}
  </style>
</head>
<body>
  <header>
    <h1>Manim 多场景实时预览</h1>
    <div class="sub">保存代码自动渲染并刷新。若无画面，请查看对应日志。</div>
  </header>
  <main>
    <div class="grid">
      {cards}
    </div>
  </main>
  <div class="hint">日志目录：<code>{log_dir}</code></div>
  <script src="/livereload.js?port={port}&mindelay=300&v=2"></script>
</body>
</html>
"""

def make_card(name: str, src_name: str, scene_name: str, ts: int) -> str:
    return f"""
    <div class="card">
      <header>
        <div class="title">{name}</div>
        <div class="meta"><code>{src_name}</code> · <code>{scene_name}</code></div>
      </header>
      <div class="player">
        <video src="/previews/{name}.mp4?ts={ts}" controls autoplay loop muted playsinline></video>
      </div>
    </div>
    """

def write_dashboard(html_path: Path, targets: List[Target], port: int, log_dir: Path):
    ts = int(time.time())
    cards = "\n".join([make_card(t.name, os.path.basename(t.src), t.scene, ts) for t in targets])
    html = DASHBOARD_TEMPLATE.format(cards=cards, port=port, log_dir=str(log_dir))
    html_path.write_text(html, encoding="utf-8")

def parse_targets(args) -> List[Target]:
    targets: List[Target] = []
    previews_dir = Path.cwd() / "previews"
    logs_dir = Path.cwd() / "logs"
    previews_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    for idx, spec in enumerate(args.target):
        parts = spec.split(":")
        src = parts[0].strip()
        scene = parts[1].strip() if len(parts) >= 2 and parts[1].strip() else None

        src_path = Path(src).resolve()
        if not src_path.exists():
            err(f"找不到源文件：{src_path}")
            sys.exit(1)

        if not scene:
            info(f"[{src_path.name}] 自动检测场景名…")
            scene_auto = autodetect_scene(str(src_path))
            if not scene_auto:
                err(f"[{src_path.name}] 无法自动检测场景名，请用 'file.py:SceneName' 指定。")
                sys.exit(1)
            scene = scene_auto

        base_name = scene
        existing = {t.name for t in targets}
        if base_name in existing:
            base_name = f"{scene}_{idx+1}"

        t = Target(
            src=str(src_path),
            scene=scene,
            name=base_name,
            quality=args.quality,
            media_dir=(Path(args.media_dir).resolve() / base_name),
            preview_path=(previews_dir / f"{base_name}.mp4"),
            log_path=(logs_dir / f"{base_name}.log"),
            lock=Lock(),
            root_dir=src_path.parent.resolve(),
        )
        t.media_dir.mkdir(parents=True, exist_ok=True)
        targets.append(t)

    if not targets:
        err("未提供任何监看的目标。示例：\n  python manim_live_preview.py --target a.py:SceneA --target b.py:SceneB")
        sys.exit(1)

    return targets

class PyChangeHandler:
    def __init__(self, targets: List[Target], rebuild_fn_map: Dict[str, callable], debounce=0.35):
        from watchdog.events import FileSystemEventHandler
        class _Handler(FileSystemEventHandler):
            pass
        self.HandlerBase = _Handler
        self.targets = targets
        self.rebuild_fn_map = rebuild_fn_map
        self.debounce = debounce
        self._last: Dict[str, float] = {}

    def build(self):
        handler = self.HandlerBase()

        def on_change(path: str):
            p = Path(path)
            if p.suffix.lower() != ".py":
                return
            fired = set()
            for t in self.targets:
                try:
                    if p.resolve().is_relative_to(t.root_dir):
                        key = t.name
                        now = time.time()
                        if now - self._last.get(key, 0.0) < self.debounce:
                            continue
                        self._last[key] = now
                        fired.add(t.name)
                        self.rebuild_fn_map[t.name]()
                except Exception:
                    try:
                        if str(p.resolve()).startswith(str(t.root_dir)):
                            key = t.name
                            now = time.time()
                            if now - self._last.get(key, 0.0) < self.debounce:
                                continue
                            self._last[key] = now
                            fired.add(t.name)
                            self.rebuild_fn_map[t.name]()
                    except Exception:
                        pass
            if fired:
                info("触发重建：" + ", ".join(sorted(fired)))

        def _on_any(event):
            try:
                if hasattr(event, "src_path"):
                    on_change(event.src_path)
                if hasattr(event, "dest_path"):
                    on_change(event.dest_path)
            except Exception:
                pass

        handler.on_modified = _on_any
        handler.on_created  = _on_any
        handler.on_moved    = _on_any
        handler.on_deleted  = _on_any
        return handler

def start_watchdog_observers(targets: List[Target], handler) -> List:
    from watchdog.observers import Observer
    observers = []
    for t in targets:
        obs = Observer()
        obs.schedule(handler, str(t.root_dir), recursive=True)
        obs.start()
        ok(f"[watch] {t.name} 监听目录：{t.root_dir}")
        observers.append(obs)
    return observers

def main():
    ensure_deps()
    from livereload import Server

    parser = argparse.ArgumentParser(description="Manim 多场景实时预览")
    parser.add_argument("--target", action="append", default=[],
                        help="指定一个监看目标，可多次： file.py:SceneName ；或 file.py（自动检测第一个 Scene）")
    parser.add_argument("--quality",default="ql",choices=["ql", "qm", "qh"],
                        help="渲染质量：ql(低，默认) / qm(中) / qh(高)")
    parser.add_argument("--port", type=int, default=5500, help="HTTP 端口（默认 5500）")
    parser.add_argument("--media-dir", dest="media_dir", default=".media_multi",
                        help="Manim 的 media_dir 根目录（默认 .media_multi）")
    parser.add_argument("--no-open", dest="no_open", action="store_true",
                        help="不自动打开浏览器")
    parser.add_argument("--verbose", action="store_true",
                        help="显示 Manim 调试日志（-v DEBUG）")
    args = parser.parse_args()

    if not args.target:
        warn("未指定 --target，默认尝试 demo.py（自动检测场景）。")
        args.target = ["demo.py"]

    targets = parse_targets(args)

    dashboard = Path.cwd() / "index.html"
    write_dashboard(dashboard, targets, args.port, Path.cwd() / "logs")

    info("首次渲染所有目标…")
    for t in targets:
        render_target(t, args.verbose)

    server = Server()
    for t in targets:
        server.watch(str(t.preview_path))
    server.watch(str(dashboard))

    url = f"http://127.0.0.1:{args.port}/index.html"
    if not args.no_open:
        try:
            webbrowser.open(url, new=2)
            info(f"已请求打开浏览器：{url}")
        except Exception as e:
            warn(f"自动打开浏览器失败：{e}，请手动访问：{url}")

    rebuild_fn_map: Dict[str, callable] = {}
    debounce = 0.35
    for t in targets:
        def make_rebuild(tt: Target):
            def _rebuild():
                with tt.lock:
                    now = time.time()
                    if now - tt.last_build_t < debounce:
                        return
                    tt.last_build_t = now
                info(f"[{tt.name}] 检测到变更，开始渲染…")
                ok_render = render_target(tt, args.verbose)
                write_dashboard(dashboard, targets, args.port, Path.cwd() / "logs")
                if not ok_render:
                    warn(f"[{tt.name}] 渲染失败（保留上次预览）。详见日志：{tt.log_path}")
            return _rebuild
        rebuild_fn_map[t.name] = make_rebuild(t)

    py_handler = PyChangeHandler(targets, rebuild_fn_map).build()
    observers = start_watchdog_observers(targets, py_handler)

    def run_server():
        info(f"启动本地服务：{url}")
        server.serve(root=".", host="127.0.0.1", port=args.port, open_url_delay=None,
                     restart_delay=0.5, debug=False, live_css=False)

    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        info("正在退出…")
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join()

if __name__ == "__main__":
    main()
