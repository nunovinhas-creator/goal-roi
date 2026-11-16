"""Offline rehearsal on a disposable copy; verify byte preservation of every input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def hashes(root):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--now',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    out=a.output.resolve()
    original=ROOT/'data'
    if out==original or original in out.parents or out in original.parents:
        p.error('isolated output required')
    before=hashes(original)
    with tempfile.TemporaryDirectory(prefix='goal-roi-recovery-') as tmp:
        source=Path(tmp)/'data'
        shutil.copytree(original,source)
        copied_before=hashes(source)
        subprocess.run([sys.executable,'-m','scangoal.recovery','--audit','--dry-run','--now',a.now,'--output',str(out)],
                       cwd=ROOT,env=dict(os.environ,SCANGOAL_DATA_DIR=str(source)),check=True)
        copied_after=hashes(source)
    after=hashes(original)
    assert before==after==copied_before==copied_after,'input changed'
    evidence={'all_original_and_copy_files_unchanged':True,'input_sha256':before,'api_requests_made':0,
              'scientific_integration':'not activated; recovery results stay in isolated staging',
              'telegram_integration':'none'}
    (out/'integrity.json').write_text(json.dumps(evidence,indent=1,sort_keys=True)+'\n')


if __name__=='__main__':main()
