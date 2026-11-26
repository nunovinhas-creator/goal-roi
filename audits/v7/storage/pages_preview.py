"""Build a deterministic Pages-compatible archive locally; no deploy or credentials."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pr", required=True, type=Path)
    cli.add_argument("--report-root", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    args = cli.parse_args()
    source, report_root = args.pr.resolve(), args.report_root.resolve()
    output = args.output.resolve()
    if output.is_relative_to(source) or output.is_relative_to(report_root):
        cli.error("the preview must be outside both source checkouts")
    with tempfile.TemporaryDirectory() as temp:
        site = Path(temp) / "site"
        inputs = source / "docs"
        if any(item.is_symlink() for item in inputs.rglob("*")):
            raise SystemExit("the site contains a symlink")
        shutil.copytree(inputs, site)
        shutil.copyfile(report_root / "docs/report.json", site / "report.json")
        snapshots = site / "v6/snapshots"
        subprocess.run(["node", str(source / "scripts/build_dashboard_snapshots.cjs"),
                        "--report", str(site / "report.json"), "--prospective",
                        str(report_root / "data/prospective_report.json"), "--protocol",
                        str(report_root / "scangoal/prospective_protocol.json"), "--out", str(snapshots)],
                       check=True, capture_output=True)
        manifest = json.loads((snapshots / "dashboard-manifest.json").read_bytes())
        assert manifest["report_sha256"] == hashlib.sha256((site / "report.json").read_bytes()).hexdigest()
        for entry in manifest["files"].values():
            body = (snapshots / entry["path"]).read_bytes()
            assert len(body) == entry["bytes"] and hashlib.sha256(body).hexdigest() == entry["sha256"]
        contents = sorted(item for item in site.rglob("*") if item.is_file())
        if sum(p.stat().st_size for p in contents) >= 10**9:
            raise SystemExit("the preview exceeds the Pages published limit")
        with output.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
                for item in contents:
                    info = tarfile.TarInfo(name=item.relative_to(site).as_posix())
                    info.size, info.mode = item.stat().st_size, 0o644
                    with item.open("rb") as data:
                        tar.addfile(info, data)
        with tarfile.open(output, "r:gz") as tar:
            assert all(m.isfile() for m in tar.getmembers())
            required = {"index.html", "v6/index.html", "report.json", "v6/snapshots/dashboard-manifest.json"}
            assert required <= set(tar.getnames())
        print(json.dumps({"members": len(contents), "site_bytes": sum(x.stat().st_size for x in contents),
                          "compressed_bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                          "consistent_snapshots": len(manifest["files"]), "external_deployments": 0}, indent=2))


if __name__ == "__main__":
    main()
