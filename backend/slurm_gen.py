"""
slurm_gen.py — deterministic Slurm script generator + knowledge base for
project rwth2125 (CLAIX-2023). This is the SAME logic as slurm/gen_job.sh,
ported to Python so the LLM can call it as a tool and always get correct
#SBATCH directives instead of paraphrasing them.
"""

ACCOUNT = "rwth2125"
CPUS_PER_GPU = 24
RAM_PER_GPU = 122   # GB system-RAM share per H100
VRAM_PER_GPU = 80   # GB VRAM per H100
LOGIN_NODE = "login23-1.hpc.itc.rwth-aachen.de"


def resource_summary(mode="production", gpus=1, time="04:00:00", devel=False):
    """Exact resources a request maps to — for tracking (machine, vram, ram, time)."""
    if mode == "development" and devel:
        return {"machine": "devel (CPU test)", "partition": "devel", "gpus": 0,
                "vram_gb": 0, "ram_gb": 8, "cpus": 2, "time": time,
                "hours": round(_time_hours(time), 2)}
    g = int(gpus or 1)
    return {"machine": "c23g · H100", "partition": "c23g", "gpus": g,
            "vram_gb": g * VRAM_PER_GPU, "ram_gb": g * RAM_PER_GPU,
            "cpus": g * CPUS_PER_GPU, "time": time,
            "hours": round(_time_hours(time), 2)}

# --- Safety caps: refuse oversized requests --------------------------------
MAX_GPUS = 4        # one c23g node = 4 H100; no multi-node here
MAX_HOURS = 24      # don't let a single job hog a GPU for too long
MAX_DEVEL_HOURS = 1 # devel is for short tests only


def _time_hours(t):
    """Parse Slurm time (D-HH:MM:SS / HH:MM:SS / MM:SS) into hours."""
    t = str(t).strip()
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
    parts = [int(p) for p in t.split(":")]
    while len(parts) < 3:
        parts = [0] + parts
    h, m, s = parts
    return days * 24 + h + m / 60.0 + s / 3600.0


def _guard(gpus, time, max_gpus=MAX_GPUS, max_hours=MAX_HOURS):
    """Raise ValueError if the request is too large."""
    if int(gpus) > max_gpus:
        raise ValueError(
            f"GPU request ({gpus}) exceeds the safety cap of {max_gpus} "
            f"(one c23g node has 4 H100). Lower the GPU count.")
    hrs = _time_hours(time)
    if hrs > max_hours:
        raise ValueError(
            f"Wall time {time} (~{hrs:.1f} h) exceeds the safety cap of "
            f"{max_hours} h. Shorten it or use a chain job for long runs.")

# name -> (hpc_username, full_name). Edit if the mapping is wrong.
TEAM = {
    "mayur":   ("kbp11750", "Mayur Waghchoure"),
    "madhava": ("xco30720", "Madhava"),
}


def generate_script(user="mayur", mode="production", gpus=1,
                    time="04:00:00", job_name="rwth2125_job", command=""):
    """Return a ready-to-submit #!/usr/bin/zsh batch script as a string."""
    user = (user or "mayur").lower()
    if user not in TEAM:
        raise ValueError(f"unknown user '{user}'. Choose: {', '.join(TEAM)}")
    uname, ufull = TEAM[user]

    mode = (mode or "production").lower()
    if not command:
        command = "python main.py   # <-- replace with your command"

    if mode == "devel":
        _guard(0, time, max_gpus=0, max_hours=MAX_DEVEL_HOURS)
        partition = "devel"
        gpus = 0
        cpus = 2
        acct_line = "# (devel: no --account on purpose)"
        gres_line = "# (devel: no GPU guarantee; omit --gres)"
        vram = "n/a"
        ram = 8
        module_line = "# module load CUDA   # (uncomment if your test needs it)"
        gpu_check = "# (no GPU on devel)"
        prefix = "devel"
    else:
        partition = "c23g"
        gpus = int(gpus)
        if gpus < 1:
            raise ValueError("gpus must be at least 1 for a production job")
        _guard(gpus, time)
        cpus = gpus * CPUS_PER_GPU
        ram = gpus * RAM_PER_GPU
        vram = f"{gpus * 80} GB ({gpus} x H100)"
        acct_line = f"#SBATCH --account={ACCOUNT}"
        gres_line = f"#SBATCH --gres=gpu:{gpus}"
        module_line = "module load CUDA"
        gpu_check = "nvidia-smi"
        prefix = "prod"

    return f"""#!/usr/bin/zsh
###############################################################################
# Generated for project rwth2125 (CLAIX-2023)
# Owner     : {ufull}  (HPC user: {uname})
# Mode      : {mode}
# Partition : {partition}
# GPUs      : {gpus}    VRAM: {vram}
# CPUs      : {cpus}    (~{ram} GB RAM share)
# Wall time : {time}
# Submit    : sbatch <thisfile>     Monitor: squeue --me
###############################################################################

#SBATCH --job-name={job_name}
{acct_line}
#SBATCH --partition={partition}
{gres_line}
#SBATCH --cpus-per-task={cpus}
#SBATCH --time={time}
#SBATCH --output=logs/{uname}_{prefix}_%j.out

echo "=== Job $SLURM_JOB_ID on $(hostname) | start $(date) ==="

module purge
{module_line}
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate <env>

{gpu_check}

# cd /work/rwth2125/repos/<your-repo> || exit 1
{command}

echo "=== Done $(date) ==="
"""


def generate_salloc(user="mayur", gpus=1, time="04:00:00", devel=False):
    """Return an interactive `salloc` command for hands-on development.

    devel=True  -> short test on the devel partition (no account, no GPU promise)
    devel=False -> interactive H100 on c23g, billed to the project.
    """
    user = (user or "mayur").lower()
    if user not in TEAM:
        raise ValueError(f"unknown user '{user}'. Choose: {', '.join(TEAM)}")

    if devel:
        _guard(0, time, max_gpus=0, max_hours=MAX_DEVEL_HOURS)
        return (
            "# Interactive devel test (run on a login node, in a FRESH shell):\n"
            f"salloc -p devel -n 2 -t {time}\n"
            "#   -> lands you on a compute node. Type 'exit' to release it."
        )
    gpus = int(gpus)
    if gpus < 1:
        raise ValueError("gpus must be at least 1")
    _guard(gpus, time)
    cpus = gpus * CPUS_PER_GPU
    return (
        "# Interactive H100 session (run on a login node, in a FRESH shell):\n"
        f"salloc --account={ACCOUNT} --partition=c23g "
        f"--gres=gpu:{gpus} --cpus-per-task={cpus} --time={time}\n"
        "# Then on the node:  module load CUDA && nvidia-smi\n"
        "# 'exit' releases the allocation. (salloc injects SLURM_* env vars that\n"
        "#  linger afterwards, so always use a fresh shell.)"
    )


# --- Tool schema advertised to the Groq/OpenAI-compatible model -------------
SALLOC_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_salloc_command",
        "description": (
            "Generate an interactive `salloc` command. Use this for DEVELOPMENT, "
            "debugging, quick tests, or any hands-on interactive work — the user "
            "should develop interactively with salloc before writing an sbatch "
            "production script. For real long-running data-generation/training "
            "jobs use generate_slurm_script instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "enum": ["mayur", "madhava"]},
                "devel": {"type": "boolean",
                          "description": "True = short test on devel partition "
                                         "(no account, no GPU guarantee). False = "
                                         "interactive H100 on c23g."},
                "gpus": {"type": "integer", "minimum": 1, "maximum": 4,
                         "description": "H100 count (ignored when devel=True)."},
                "time": {"type": "string",
                         "description": "Wall time HH:MM:SS."},
            },
            "required": [],
        },
    },
}

def generate_dev_script(user="mayur", gpus=1, time="04:00:00", devel=False):
    """A runnable .sh for DEVELOPMENT: starts an interactive salloc session.
    Run it directly with ./dev_session.sh — NOT with sbatch."""
    user = (user or "mayur").lower()
    if user not in TEAM:
        raise ValueError(f"unknown user '{user}'. Choose: {', '.join(TEAM)}")
    uname = TEAM[user][0]

    if devel:
        _guard(0, time, max_gpus=0, max_hours=MAX_DEVEL_HOURS)
        salloc = f"salloc -p devel -n 2 -t {time}"
        head = "devel test (CPU, no GPU, short queue)"
        gpu_hint = "# (no GPU on devel)"
    else:
        gpus = int(gpus)
        if gpus < 1:
            raise ValueError("gpus must be at least 1")
        _guard(gpus, time)
        cpus = gpus * CPUS_PER_GPU
        salloc = (f"salloc --account={ACCOUNT} --partition=c23g "
                  f"--gres=gpu:{gpus} --cpus-per-task={cpus} --time={time}")
        head = f"interactive H100 session ({gpus} GPU, {gpus*VRAM_PER_GPU} GB VRAM)"
        gpu_hint = "module load CUDA && nvidia-smi"

    return f"""#!/usr/bin/zsh
###############################################################################
# Development session for {user} (HPC user: {uname}) — {head}
# RUN IT DIRECTLY (interactive):   chmod +x dev_session.sh && ./dev_session.sh
# Do NOT use sbatch — this is interactive and drops you onto the compute node.
# Tip: use a FRESH shell; salloc injects SLURM_* vars that linger afterwards.
###############################################################################

{salloc}

# Once the allocation is granted you are ON the compute node. Then:
#   {gpu_hint}
#   ... develop / debug interactively ...
# Type 'exit' to release the allocation.
"""


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_slurm_script",
        "description": (
            "Generate a correct Slurm batch script for the RWTH CLAIX-2023 "
            "cluster, project rwth2125. Use this whenever the user asks for a "
            "job script, sbatch file, or wants to run something on the GPU."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "enum": ["mayur", "madhava"],
                         "description": "Which team member owns the job."},
                "mode": {"type": "string", "enum": ["devel", "production"],
                         "description": "devel = 5-min test, no billing, no GPU "
                                        "guarantee. production = real GPU work."},
                "gpus": {"type": "integer", "minimum": 1, "maximum": 4,
                         "description": "Number of H100 (80GB each). 1=80GB VRAM, "
                                        "2=160GB, 4=320GB. Ignored for devel."},
                "time": {"type": "string",
                         "description": "Wall time HH:MM:SS or D-HH:MM:SS."},
                "job_name": {"type": "string"},
                "command": {"type": "string",
                            "description": "Shell command(s) to run in the job."},
            },
            "required": ["mode"],
        },
    },
}


# --- Static template library (returned verbatim on request) -----------------
TEMPLATES = {
    "test_devel": "00_test_devel.sh — 5-min smoke test on the devel partition",
    "gpu_job": "01_gpu_job.sh — single H100 production job",
    "multi_gpu_train": "02_multi_gpu_train.sh — 4x H100 training",
    "webapp": "03_webapp.sh — LLM web app + SSH tunnel hint",
}


SYSTEM_PROMPT = f"""You are the HPC assistant for project **rwth2125** \
(ID 160448917, "Scalable Synthetic Experience, Multimodal Robotics Foundation \
Models, and World-Model-Based Autonomous Execution for Physical AI") on the \
RWTH **CLAIX-2023** cluster. You help the team write and understand Slurm jobs.

Hard facts — never contradict these:
- GPU partition is **c23g**: nodes have 4x NVIDIA **H100 (80 GB VRAM each)**, \
no MIG slicing. So VRAM is chosen by GPU COUNT: 1=80GB, 2=160GB, 3=240GB, 4=320GB.
- 1 GPU is capped at ~{CPUS_PER_GPU} CPUs and ~{RAM_PER_GPU} GB RAM. More needs more GPUs.
- SAFETY CAPS (enforced): max **{MAX_GPUS} GPUs**, max **{MAX_HOURS} h** wall time \
(devel max {MAX_DEVEL_HOURS} h). If a user asks for more, do NOT bypass it — explain \
the cap, suggest the max allowed or splitting into a chain job, then proceed within limits. \
The tools return an ERROR for oversized requests; relay it plainly.
- **devel** partition is for ≤~5-min tests: do NOT add --account, no GPU guarantee.
- production jobs add `#SBATCH --account={ACCOUNT}`.
- Shebang is always `#!/usr/bin/zsh` (cluster default shell is zsh).
- Storage: /home/rwth2125 (250GB, backed up, code) · /work/rwth2125 (250GB, no \
backup, repos/scratch) · /hpcwork/rwth2125 (1TB, no backup, datasets/models/checkpoints).
- Team: Mayur -> user kbp11750 ; Madhava -> user xco30720.
- Web apps run on a compute node; reach them with an SSH tunnel through {LOGIN_NODE}.
- Workflow order: salloc interactive -> devel test -> production sbatch.

Choosing the right tool:
- For DEVELOPMENT, debugging, quick interactive tests -> call \
`generate_salloc_command` (use devel=True for ≤5-min throwaway tests on the \
devel partition; devel=False for an interactive H100 on c23g).
- For SERIOUS, long-running, unattended work (data generation, dataset builds, \
training, batch inference) -> call `generate_slurm_script` to produce an \
sbatch file. Never write #SBATCH lines yourself.

Shared project & filesystems (answer these from the reference below, accurately):
- Project storage is auto-shared with the whole rwth2125 UNIX group at \
/home/rwth2125, /work/rwth2125, /hpcwork/rwth2125 (setgid + umask 007, so files \
are group-accessible automatically). Check quota with `r_quota -u rwth2125`.
- Put git repos & code on /work/rwth2125/repos ; big datasets/models/checkpoints \
on /hpcwork/rwth2125 (1TB, fast I/O). Keep irreplaceable results on /home (backed up).
- Git tracks code/configs/Dockerfiles ONLY — never commit datasets/checkpoints/outputs.

Moving data:
- Laptop <-> cluster: use the dedicated COPY nodes copy23-1 / copy23-2, not login \
nodes. Prefer `rsync -aP -e ssh ...`, or `sftp`, or WinSCP (Windows). For \
$WORK/$HPCWORK give the full path. Consolidate many small files into one tar first.
- Cluster <-> another cluster: `rclone` configured on copy23.
- Code from Git: `cd /work/rwth2125/repos && git clone <url>` on a login node.

Features & scaffolding:
- The team builds features in separate repos first, then integrates into the \
webapp. When the user says which feature they are working on (e.g. DataForge, \
GroundingSAM2, NVBlox, cuVSLAM, VLM inference, fine-tuning, the webapp itself, or \
a custom one), CALL `scaffold_feature` to create a tuned repo skeleton + Slurm script.

Environment / diagnostics (IMPORTANT — this is a no-root HPC system):
- NEVER suggest `sudo`, `apt`, `apt-get`, `yum`, or `pip install` as root. Software \
is provided via the **module system**: `module spider <name>` to find it, \
`module load <name>` to use it. git is normally already on PATH.
- To check GPUs use `nvidia-smi` (only works on a compute node, not login). CUDA \
toolkit version is `nvcc --version`; driver version shows in `nvidia-smi`.
- Docker is usually unavailable (needs root). Use **Apptainer** (`module load \
Apptainer`, `apptainer run --nv image.sif`). There is no docker-compose equivalent; \
run services as separate apptainer instances or sbatch steps.
- Give these from the reference block; do not invent package-manager commands.

After a tool returns, present commands/scripts in a ```bash code block and add \
one or two practical lines (how to submit/monitor, or what path to use). Be \
concise. When asked about filesystems, shared storage, or data transfer, give \
the exact command for their case.

The user's team should NOT need to memorise commands. Whenever you output a \
runnable sbatch SCRIPT (not a one-line salloc), end your message with this short \
"Run it on the cluster" footer so they know the path (the UI gives them a \
download .sh button):
  1. Click ⬇ .sh to download the script, drop it into your feature repo, then \
`git add`, `git commit`, `git push`.
  2. On an HPC login node: `cd /work/rwth2125/repos/<repo> && git pull`
  3. Run it: `sbatch <file>.sh`  ·  watch it: `squeue --me`
Mention the quick alternative once if relevant: `rsync -aP -e ssh <file>.sh \
<user>@copy23-1.hpc.itc.rwth-aachen.de:/work/rwth2125/repos/<repo>/` then sbatch.

If the user wants the WHOLE round-trip done for them (a proper project folder + \
git push + HPC pull + run), tell them to click the **📦 Bundle** button in the top \
bar: it downloads a ready .zip with the job script and `scripts/local_setup.sh` \
(git init+commit+push) and `scripts/hpc_pull_run.sh` (clone/pull+sbatch)."""

# --- Copy-paste reference the model can surface verbatim --------------------
REFERENCE = f"""DATA TRANSFER (run on your LAPTOP unless noted)
# Upload a folder to project scratch:
rsync -aP -e ssh ./mydata <user>@copy23-1.hpc.itc.rwth-aachen.de:/work/rwth2125/
# Upload a big dataset to fast storage:
rsync -aP -e ssh ./dataset.tar <user>@copy23-1.hpc.itc.rwth-aachen.de:/hpcwork/rwth2125/datasets/
# Download results back:
rsync -aP -e ssh <user>@copy23-1.hpc.itc.rwth-aachen.de:/hpcwork/rwth2125/results/ ./results/
# Interactive file transfer:
sftp <user>@copy23-1.hpc.itc.rwth-aachen.de   # then: cd /hpcwork/rwth2125 ; put f ; get f

GET A DOWNLOADED SCRIPT ONTO THE CLUSTER AND RUN IT
# Path A — via Git (recommended, reproducible). On your LAPTOP:
#   move the downloaded run.sh into your repo, then:
git add run.sh && git commit -m "add slurm script" && git push
# On an HPC LOGIN node:
cd /work/rwth2125/repos/<repo> && git pull && sbatch run.sh && squeue --me
# Path B — quick copy (no git). On your LAPTOP:
rsync -aP -e ssh run.sh <user>@copy23-1.hpc.itc.rwth-aachen.de:/work/rwth2125/repos/<repo>/
# then on a login node:  cd /work/rwth2125/repos/<repo> && sbatch run.sh

GIT (run on a LOGIN node)
cd /work/rwth2125/repos && git clone <repo-url>

CHECK STORAGE
r_quota                 # your personal quotas
r_quota -u rwth2125     # the shared project quotas

SHARED FOLDER LAYOUT
/work/rwth2125/{{repos,docker,docs}}
/hpcwork/rwth2125/{{datasets,models,checkpoints,results,logs}}

GPU / CUDA DIAGNOSTICS (run ON a compute node, inside salloc/sbatch)
nvidia-smi                      # GPU usage, memory, driver version, running procs
nvidia-smi -l 2                 # live-refresh every 2s
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv
nvcc --version                  # CUDA TOOLKIT (compiler) version
python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"

MODULES — how you "install" software on HPC (NO sudo / apt)
module spider cuda              # find available CUDA toolkit modules + versions
module spider git               # find git modules (a system git is usually already on PATH)
module load CUDA                # load it for this session
module list                     # what's currently loaded
module purge                    # unload everything (start clean)
git --version                   # check the git already on PATH

CONTAINERS — Docker is usually NOT available (no root). Use Apptainer.
which docker || echo "no docker (expected on HPC)"
module spider Apptainer         # find the Apptainer module
module load Apptainer
apptainer build my.sif docker://nvidia/cuda:12.4.0-runtime-ubuntu22.04
apptainer run --nv my.sif       # --nv exposes the H100 GPUs inside the container
# docker-compose has no direct HPC equivalent; run services as separate
# apptainer instances or as steps in your sbatch script.
"""
