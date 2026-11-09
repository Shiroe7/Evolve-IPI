#!/bin/bash
# Run from tmux under a resource-limited systemd user scope. No system/base environment edits.
set -euo pipefail
task_root=$(realpath -- "${1:?Supply a dedicated directory under your home}")
source_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
case "$task_root/" in "$HOME/"*) ;; *) echo 'Prefix must be in your own home'; exit 2;; esac
conda_bin="$task_root/miniforge3/bin/conda"
test -x "$conda_bin"
export CONDA_PKGS_DIRS="$task_root/conda-pkgs"
export PIP_CACHE_DIR="$task_root/pip-cache"
export MAMBA_EXTRACT_THREADS=2
main_env="$task_root/envs/main"
meta_env="$task_root/envs/metaclaw"
if test ! -x "$main_env/bin/python"; then
    "$conda_bin" create -y --prefix "$main_env" --override-channels \
        -c https://conda.anaconda.org/conda-forge python=3.12.13 pip
fi
"$main_env/bin/python" -m pip install --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://mirrors.aliyun.com/pypi/simple 'torch==2.14.0+cpu'
"$main_env/bin/python" -m pip install --index-url https://mirrors.aliyun.com/pypi/simple \
    -e "$source_root" pytest==9.1.1
"$main_env/bin/python" -m pip check
if test ! -x "$meta_env/bin/python"; then
    "$conda_bin" create -y --offline --prefix "$meta_env" --clone "$main_env"
fi
"$meta_env/bin/python" -m pip install --index-url https://mirrors.aliyun.com/pypi/simple \
    --no-deps -r "$source_root/docker/requirements-metaclaw.txt"
"$meta_env/bin/python" -m pip install --index-url https://mirrors.aliyun.com/pypi/simple \
    --no-deps --no-build-isolation -e "$source_root/third_party/MetaClaw"
"$meta_env/bin/python" -m pip check
for env_dir in "$main_env" "$meta_env"; do
    "$env_dir/bin/python" -c 'import torch; assert torch.version.cuda is None; print(torch.__version__)'
    "$env_dir/bin/python" -m pip freeze > "$env_dir/installed-freeze.txt"
    "$conda_bin" list --prefix "$env_dir" --explicit > "$env_dir/conda-explicit.txt"
done
echo 'Environment installation and dependency checks complete'
