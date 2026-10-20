"""版本一致性检查（对齐官方 check_plugin_versions.py 的门禁）。

检查 package.v3.json 与各插件源码：
  1. 索引 version == 插件类 plugin_version；
  2. history 顶部条目 == v<version>；
  3. 插件目录存在且目录名 == 插件 ID 的小写；
  4. 两个插件都不允许再声明 release=true（本仓库没有自动打包工作流）。

用法：python scripts/check_versions.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "package.v3.json"


def class_version(plugin_dir: Path) -> str:
    src = (plugin_dir / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'plugin_version\s*=\s*"([^"]+)"', src)
    if not m:
        raise SystemExit(f"[FAIL] {plugin_dir}/__init__.py 缺少 plugin_version")
    return m.group(1)


def main() -> int:
    pkg = json.loads(PKG.read_text(encoding="utf-8"))
    errors = []
    for plugin_id, meta in pkg.items():
        plugin_dir = ROOT / "plugins.v3" / plugin_id.lower()
        if not (plugin_dir / "__init__.py").exists():
            errors.append(f"{plugin_id}: 缺少 plugins.v3/{plugin_id.lower()}/__init__.py")
            continue
        try:
            ver = class_version(plugin_dir)
        except SystemExit as err:
            errors.append(str(err))
            continue
        if ver != meta.get("version"):
            errors.append(f"{plugin_id}: package.v3.json version={meta.get('version')} "
                          f"!= plugin_version={ver}")
        top = next(iter(meta.get("history") or {}), None)
        expect = f"v{meta.get('version')}"
        if top != expect:
            errors.append(f"{plugin_id}: history 顶部是 {top}，应为 {expect}")
        if meta.get("release"):
            errors.append(f"{plugin_id}: 声明了 release=true，但本仓库没有自动打包工作流，"
                          "请改为 false 或补 Release 压缩包")
        print(f"  OK  {plugin_id} @ v{meta.get('version')}")

    if errors:
        print()
        for e in errors:
            print("[FAIL]", e)
        return 1
    print(f"\n通过：{len(pkg)} 个插件版本一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
