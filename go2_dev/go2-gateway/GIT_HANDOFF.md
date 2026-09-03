# Git Handoff

Current project directory:

```text
<workspace-root>\go2_dev\go2-gateway
```

Target GitHub repository provided by the user:

```text
https://github.com/gq18262121731-source/unitree_go
```

## Current Git State

This directory is not currently inside a Git repository.

Verified commands:

```powershell
git rev-parse --show-toplevel
```

It returns:

```text
fatal: not a git repository (or any of the parent directories): .git
```

The only Git repositories found under the local `go2_dev` directory are upstream Unitree dependency checkouts:

```text
unitree_ros2
unitree_sdk2
unitree_sdk2_python
unitree_webrtc_connect
```

They should not be used as the application repository for this gateway.

## Recommended Push Steps

Run these commands from the project directory after confirming that `gq18262121731-source/unitree_go` is the intended application repository:

```powershell
cd "<workspace-root>\go2_dev\go2-gateway"

python scripts\verify_release.py

git init
git branch -M main
git remote add origin https://github.com/gq18262121731-source/unitree_go.git

git add .
git status --short
git commit -m "Build Go2 elder-care task gateway"
git push -u origin main
```

If the GitHub repository already has commits, pull or clone it first and copy this project into that working tree before pushing, so the remote history is not overwritten.

## Release Check

Latest local release check before this handoff:

```powershell
python scripts\verify_release.py
```

Expected result:

```text
release verification passed
```

The mock HTTP fall-response smoke test was also verified with an already running gateway on `127.0.0.1:8097`.
