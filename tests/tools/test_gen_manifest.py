"""gen_manifest 測試：latest.json 結構、版號與直連 URL 正確。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gm = _load("gen_manifest", "tools/gen_manifest.py")


def test_build_explicit_versions():
    m = gm.build(app_ver="1.2.3", ext_ver="4.5.6")
    assert m["app"]["version"] == "1.2.3"
    assert m["app"]["tag"] == "app-v1.2.3"
    assert m["app"]["exe"].endswith("/app-v1.2.3/CodexAutoAI-setup-1.2.3.exe")
    assert m["ext"]["version"] == "4.5.6"
    assert m["ext"]["tag"] == "ext-v4.5.6"
    assert m["ext"]["vsix"].endswith("/ext-v4.5.6/codexautoai-4.5.6.vsix")


def test_urls_point_to_public_mirror():
    m = gm.build(app_ver="0.2.3", ext_ver="0.2.3")
    for url in (m["app"]["exe"], m["ext"]["vsix"]):
        assert url.startswith("https://github.com/gsinvest017-ai/gs-codex-auto-ai-releases/releases/download/")


def test_reads_local_versions():
    m = gm.build()  # 讀 desktop/VERSION + package.json
    assert m["app"]["version"] and m["ext"]["version"]
    # 版號格式 x.y.z
    for v in (m["app"]["version"], m["ext"]["version"]):
        parts = v.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts)
