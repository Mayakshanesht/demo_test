"""
bundle.py — build a downloadable project ZIP that implements the full round-trip
the team wants with NO commands to memorise:

  laptop:  unzip -> ./scripts/local_setup.sh   (git init + commit + push)
  HPC:     ./scripts/hpc_pull_run.sh            (clone/pull + sbatch + squeue)
  HPC(1x): ./scripts/hpc_filesystem_setup.sh    (build shared project folders)

The zip contains a proper, ready-to-run feature folder with a tuned Slurm job.
"""

import io
import zipfile

import features as ft
import slurm_gen as sg

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


def _hpc_fs_setup():
    return """#!/usr/bin/zsh
# Run ONCE on an HPC login node to build the shared project folder layout.
# Project dirs already have setgid + umask 007, so files are group-shared.
set -e
echo ">> groups:"; groups
echo ">> project quota:"; r_quota -u rwth2125 || true

mkdir -p /work/rwth2125/{repos,docker,docs}
mkdir -p /hpcwork/rwth2125/{datasets,models,checkpoints,results,logs}

echo ">> Created:"
echo "   /work/rwth2125/{repos,docker,docs}"
echo "   /hpcwork/rwth2125/{datasets,models,checkpoints,results,logs}"
ls -ld /work/rwth2125/repos /hpcwork/rwth2125/datasets
"""


def _readme(feature, user, remote, branch, gpus, time):
    return f"""# {feature} — WestAI HPC Assistant bundle

Owner: {user} (git branch: **{branch}**) · Project rwth2125 · CLAIX-2023 (c23g / H100)
Resources: {gpus} GPU, {time}
Commands repo: {remote}

## Workflow (no commands to memorise)

1. **On your laptop** — push this to your branch of the commands repo:
   ```bash
   ./scripts/local_setup.sh           # uses remote {remote} on branch {branch}
   ```
2. **On an HPC login node** — pull your branch and run:
   ```bash
   ./scripts/hpc_pull_run.sh
   ```
3. (Once per project) build shared storage on HPC:
   ```bash
   ./scripts/hpc_filesystem_setup.sh
   ```

Mayur and Madhava each work on their own branch, so pushes/pulls don't collide.
Monitor: `squeue --me` · efficiency after finish: `seff <jobid>`

## Contents
- `slurm/run.sh` — tuned sbatch job for this feature
- `{ft.FEATURES[feature]['command'].split()[0] if feature in ft.FEATURES else 'src'}` entrypoint, `configs/`, `.gitignore`
- `scripts/` — local_setup, hpc_pull_run, hpc_filesystem_setup
"""


def build_bundle(feature="custom", user="mayur", gpus=None, time=None,
                 remote=DEFAULT_REMOTE, branch=None):
    """Return (filename, zip_bytes) for the feature project bundle."""
    feature = (feature or "custom").lower()
    if feature not in ft.FEATURES:
        raise ValueError(f"unknown feature '{feature}'")
    prof = ft.FEATURES[feature]
    gpus = int(gpus) if gpus else prof["gpus"]
    time = time or prof["time"]
    remote = remote or DEFAULT_REMOTE
    branch = branch or (user or "mayur").lower()   # per-user branch
    pkg = feature.replace("-", "_")

    run_sh = sg.generate_script(user=user, mode="production", gpus=gpus,
                                time=time, job_name=feature, command=prof["command"])

    root = feature
    files = {
        f"{root}/README.md": _readme(feature, user, remote, branch, gpus, time),
        f"{root}/slurm/run.sh": run_sh,
        f"{root}/scripts/local_setup.sh": _local_setup(feature, remote, branch),
        f"{root}/scripts/hpc_pull_run.sh": _hpc_pull_run(feature, remote, branch),
        f"{root}/scripts/hpc_filesystem_setup.sh": _hpc_fs_setup(),
        f"{root}/{pkg}/__init__.py": "",
        f"{root}/{pkg}/run.py": (
            f'"""Entrypoint for {feature}. Keep importable for phase-2 webapp '
            f'integration."""\n\n\ndef main():\n    print("{feature}: TODO")\n\n\n'
            f'if __name__ == "__main__":\n    main()\n'),
        f"{root}/configs/default.yaml": f"# {feature} config\n",
        f"{root}/.gitignore": "data/\nlogs/\n*.ckpt\n__pycache__/\n*.pyc\n",
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            info = zipfile.ZipInfo(path)
            # make shell scripts executable inside the zip (rwxr-xr-x)
            info.external_attr = (0o755 if path.endswith(".sh") else 0o644) << 16
            z.writestr(info, content)
    buf.seek(0)
    return f"{feature}_bundle.zip", buf.getvalue()
