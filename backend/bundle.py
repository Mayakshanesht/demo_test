"""
bundle.py — build a downloadable project ZIP that implements the full round-trip
the team wants with NO commands to memorise:

  laptop:  unzip -> ./scripts/local_setup.sh   (git init + commit + push)
  HPC:     ./scripts/hpc_pull_run.sh            (clone/pull + sbatch + squeue)
  HPC(1x): ./scripts/hpc_filesystem_setup.sh    (build shared project folders)

The zip contains a proper, ready-to-run feature folder with a tuned Slurm job.
"""

import datetime
import io
import json
import re
import zipfile

import features as ft
import slurm_gen as sg


def _slug(s):
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (s or "").strip()).strip("-")
    return s.lower() or "job"


def new_run_id():
    return datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")

# The COMMANDS repo: where Mayur & Madhava push/pull generated scripts, each on
# their OWN branch. (The webapp lives in a separate repo, WestAI_hpc_assistant.)
DEFAULT_REMOTE = "git@github.com:CloudBeeRobotics/west_ai_hpc_commands.git"
COMMANDS_DIR = "west_ai_hpc_commands"
REPOS_ROOT = "/work/rwth2125/repos"


def _local_setup(feature, remote, branch):
    return f"""#!/usr/bin/env bash
# Run on your LAPTOP from inside this folder. Clones the shared COMMANDS repo,
# drops this feature into it on YOUR branch ({branch}), commits and pushes.
# Usage: ./scripts/local_setup.sh [git-remote-url] [branch]
set -euo pipefail
REMOTE="${{1:-{remote}}}"
BRANCH="${{2:-{branch}}}"
FEATURE="{feature}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"        # this unzipped feature folder
WORK="$HERE/../{COMMANDS_DIR}"

if [ ! -d "$WORK/.git" ]; then
  git clone "$REMOTE" "$WORK"
fi
cd "$WORK"
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH"
git pull --ff-only origin "$BRANCH" 2>/dev/null || true

# copy the feature into the repo (preserves repo history; no nested .git)
rsync -a --exclude '.git' "$HERE/" "./$FEATURE/"
git add "$FEATURE"
git commit -m "Update $FEATURE ({branch})" || echo "nothing to commit"
git push -u origin "$BRANCH"
echo "Pushed $FEATURE to $REMOTE on branch $BRANCH."
"""


def _hpc_pull_run(feature, remote, branch):
    return f"""#!/usr/bin/zsh
# Run on an HPC LOGIN node. Pulls YOUR branch ({branch}) of the commands repo
# and submits the job. Usage: ./scripts/hpc_pull_run.sh [git-remote-url] [branch]
REMOTE="${{1:-{remote}}}"
BRANCH="${{2:-{branch}}}"
FEATURE="{feature}"

mkdir -p {REPOS_ROOT}
cd {REPOS_ROOT}
if [ -d "{COMMANDS_DIR}/.git" ]; then
  cd {COMMANDS_DIR}
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  git clone "$REMOTE" {COMMANDS_DIR} && cd {COMMANDS_DIR} && git checkout "$BRANCH"
fi

cd "$FEATURE"
mkdir -p logs
sbatch slurm/run.sh
squeue --me
echo "Submitted $FEATURE (branch $BRANCH). Logs: {REPOS_ROOT}/{COMMANDS_DIR}/$FEATURE/logs/"
"""


def _readme(job_dir, feature, user, remote, branch, gpus, time, run_id):
    return f"""# {job_dir} — WestAI HPC Assistant

Run id: **{run_id}** · Feature: {feature} · Owner: {user} (git branch: **{branch}**)
Project rwth2125 · CLAIX-2023 (c23g / H100) · Resources: {gpus} GPU, {time}
Commands repo: {remote}

## Workflow (no commands to memorise)

1. **On your laptop** — push this run to your branch of the commands repo:
   ```bash
   ./scripts/local_setup.sh
   ```
2. **On an HPC login node** — pull your branch and run:
   ```bash
   ./scripts/hpc_pull_run.sh
   ```

Each run lives in its own folder (`{job_dir}`) on your branch, so nothing is
overwritten — your branch history is the record of every job you generated.
Monitor: `squeue --me` · efficiency after finish: `seff <jobid>`

## Contents
- `meta.json` — run metadata (id, user, feature, resources, timestamp)
- `slurm/run.sh` — tuned sbatch job
- `scripts/` — local_setup (git push), hpc_pull_run (pull + sbatch)
"""


def build_bundle(feature="custom", user="mayur", gpus=None, time=None,
                 remote=DEFAULT_REMOTE, branch=None, label=None, run_id=None):
    """Return (filename, zip_bytes, meta) for a uniquely-named run bundle."""
    feature = (feature or "custom").lower()
    if feature not in ft.FEATURES:
        raise ValueError(f"unknown feature '{feature}'")
    prof = ft.FEATURES[feature]
    gpus = int(gpus) if gpus else prof["gpus"]
    time = time or prof["time"]
    remote = remote or DEFAULT_REMOTE
    branch = branch or (user or "mayur").lower()   # per-user branch
    run_id = run_id or new_run_id()
    label = _slug(label or feature)
    job_dir = f"{label}_{run_id}"                  # unique per run -> trackable

    run_sh = sg.generate_script(user=user, mode="production", gpus=gpus,
                                time=time, job_name=label, command=prof["command"])

    res = sg.resource_summary("production", gpus, time)
    meta = {
        "run_id": run_id, "label": label, "feature": feature, "user": user,
        "branch": branch, "mode": "production",
        "machine": res["machine"], "gpus": res["gpus"], "vram_gb": res["vram_gb"],
        "ram_gb": res["ram_gb"], "cpus": res["cpus"], "time": time,
        "hours": res["hours"],
        "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "remote": remote,
    }

    root = job_dir
    files = {
        f"{root}/README.md": _readme(job_dir, feature, user, remote, branch, gpus, time, run_id),
        f"{root}/meta.json": json.dumps(meta, indent=2),
        f"{root}/slurm/run.sh": run_sh,
        f"{root}/scripts/local_setup.sh": _local_setup(job_dir, remote, branch),
        f"{root}/scripts/hpc_pull_run.sh": _hpc_pull_run(job_dir, remote, branch),
        f"{root}/configs/default.yaml": f"# {feature} config\n",
        f"{root}/.gitignore": "data/\nlogs/\n*.ckpt\n__pycache__/\n*.pyc\n",
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            info = zipfile.ZipInfo(path)
            info.external_attr = (0o755 if path.endswith(".sh") else 0o644) << 16
            z.writestr(info, content)
    buf.seek(0)
    return f"{job_dir}.zip", buf.getvalue(), meta
