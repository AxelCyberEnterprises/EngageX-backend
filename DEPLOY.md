# EngageX API Deployment

## 1. Overview

This document describes how the EngageX backend is actually deployed today. The old
Elastic Beanstalk-based process this file used to describe is **decommissioned** — there
are no active Elastic Beanstalk environments left in the account. Ignore any instructions
elsewhere that reference EB.

The current pipeline is manual and has two stages:

1. **Build & publish** — a Docker image is built and pushed to ECR.
2. **Rollout** — whatever runs production (ECS) is manually pointed at the new image tag.
   This repo/process does not automate that second stage.

There is also a checked-in GitHub Actions workflow (`.github/workflows/deploy.yaml`) that
builds and pushes to Docker Hub (`awarixo/engagex`) on every push to `deploy`. **Nothing in
production consumes that image** — it's a leftover from an earlier setup. Don't treat its
presence as evidence of an automated pipeline.

## 2. Why the build isn't done in CI

The Docker image is large (~4.5GB uncompressed, ~1.6GB compressed) because the Dockerfile
installs a heavy dependency stack: `build-essential`, `cmake`, `ffmpeg`, `libreoffice`, and
several Python ML/media libraries (mediapipe, opencv, librosa, etc.) on top of Django. This
is built on a temporary EC2 instance rather than a GitHub-hosted runner or a local machine.

## Before every deploy (mandatory)

On 2026-11-04 image 10.3 shipped two commits nobody had reviewed, because only the commits
not yet pushed to `origin/deploy` were checked. One of them broke every feedback report
(the transcript was read from a field nothing writes). Review **everything between what is
running and what you are about to build**, not just what is unpushed:

1. Find the commit the running image was built from. Each image carries it as a label
   (`git-sha`, see step 4 below); older images are listed in `WORK_LOG.md`:
   ```
   aws ecs describe-services --cluster engageX --services engagex-service \
     --query 'services[0].taskDefinition'
   aws ecs describe-task-definition --task-definition <that-task-def> \
     --query 'taskDefinition.containerDefinitions[0].image'
   ```
2. Read every commit and diff in that range, not just the titles:
   ```
   git log --stat <running-sha>..<sha-to-ship>
   git diff <running-sha>..<sha-to-ship>
   ```
   For anything touching report generation, transcription or the WebSocket consumer, test
   it against a real recent session's data before shipping.
3. If the range adds migrations, confirm they're applied in production (the container
   does not run `migrate` on start):
   ```
   SELECT name, applied FROM django_migrations WHERE app='<app>' ORDER BY id DESC LIMIT 5;
   ```

## 3. Build & publish steps

1. **Push the code.** Commit and push to the `deploy` branch on
   `github.com/AxelCyberEnterprises/EngageX-backend`.

2. **Provision a temporary build instance.**
   - Region: `us-west-1`, default VPC (`vpc-0d095d54d92bf9d34`), public subnet
     (`subnet-0cffd27f3533622e6`, auto-assigns a public IP).
   - AMI: latest Amazon Linux 2023 x86_64 (look it up fresh each time — don't hardcode an ID,
     it goes stale). `backend-dev-access` cannot read the SSM public AMI parameter, so use
     `aws ec2 describe-images --owners amazon --filters 'Name=name,Values=al2023-ami-2023.*-x86_64'
     --query 'sort_by(Images,&CreationDate)[-1].ImageId'`.
   - Instance type: `t3.large` (2 vCPU / 8GB — enough to build without being slow).
   - A **throwaway key pair** and a **security group scoped to SSH from your current IP
     only** (if your ISP rotates between a few egress IPs, as 41.215.247.83/.84 do, allow each
     of those /32s, otherwise ssh times out at random) — both created fresh and deleted at the end. Never reuse a long-lived key pair
     or open SSH to `0.0.0.0/0`.
   - User-data installs Docker (`dnf install -y docker && systemctl enable --now docker`).
     Docker takes a minute or two after SSH comes up; wait for `systemctl is-active docker`.
   - `ec2-user` is not in the `docker` group, so every docker command needs `sudo`.
   - The instance has no IAM role, so the preinstalled AWS CLI has no credentials. Write the
     `backend-dev-access` key to `/root/.aws/credentials` (root, because docker runs under
     sudo) before the ECR login.

3. **Get the code onto the instance.** The instance has no GitHub credentials, so don't
   `git clone` a private repo onto it. Ship the **exact reviewed commit**, never the working
   tree (a working-tree tarball silently includes uncommitted edits):
   ```
   git archive --format=tar.gz <sha-to-ship> | ssh -i <key> ec2-user@<ip> \
     'mkdir -p app && tar -xzf - -C app'
   ```

4. **Build the image**, tagged with the next version and labelled with the commit:
   ```
   sudo docker build --platform linux/amd64 --label git-sha=<sha-to-ship> \
     -t 266735827053.dkr.ecr.us-west-1.amazonaws.com/engagex:<next-tag> .
   ```
   Run this detached on the instance, logging to a file, so a dropped SSH session can't
   kill a long build. Use `setsid nohup ... > build.log 2>&1 < /dev/null &` — plain
   `nohup ... & disown` can keep the SSH command from returning. Poll the log with fresh SSH
   connections.

5. **Check the existing tags before picking the next one.** Tags in the `engagex` ECR repo
   loosely follow `<major>.<minor>`, incrementing by 0.1 per build (e.g. `9.5` → `9.6` →
   `9.7`), though the history isn't perfectly clean (there are gaps and one stray `8.91`).
   Look at the actual latest pushed tag and increment from there — don't assume from the
   version number alone:
   ```
   aws ecr describe-images --repository-name engagex \
     --query 'sort_by(imageDetails, &imagePushedAt)[-1].[imagePushedAt,imageTags]'
   ```

6. **Push to ECR**, also backgrounded on the instance:
   ```
   sudo aws ecr get-login-password --region us-west-1 \
     | sudo docker login --username AWS --password-stdin 266735827053.dkr.ecr.us-west-1.amazonaws.com
   sudo docker push 266735827053.dkr.ecr.us-west-1.amazonaws.com/engagex:<tag>
   ```

7. **Tear down the build instance immediately after the push confirms.** It only exists for
   the duration of one build:
   ```
   aws ec2 terminate-instances --instance-ids <id>
   aws ec2 delete-key-pair --key-name <temp-key>
   aws ec2 delete-security-group --group-id <temp-sg>
   ```
   Confirm nothing is left running/lingering afterward — check for stray instances, key
   pairs, and security groups matching the temp naming pattern.

## 4. Rollout (not automated)

Pushing to ECR does **not** deploy anything. The `backend-dev-access` user can do the
rollout itself (it has `ecs:RegisterTaskDefinition` and `ecs:UpdateService`):

1. Copy the running task definition, swap only the image tag, and register it as a new
   revision (strip `taskDefinitionArn`, `revision`, `status`, `requiresAttributes`,
   `compatibilities`, `registeredAt`, `registeredBy` before registering).
2. `aws ecs update-service --cluster engageX --service engagex-service --task-definition <family>:<new-rev>`
3. Wait for the PRIMARY deployment's `rolloutState` to be `COMPLETED` and 1/1 running.
4. Smoke test: a bad login to `https://api.engagexai.io/users/auth/login/` should return
   404 "No account found with this email". If the change touched reports or transcription,
   also check the next real session's report.
5. Record in `WORK_LOG.md`: image tag, git SHA, task definition revision. Rollback is
   `update-service` back to the previous revision.

## 5. Account reference

- AWS account: `266735827053`, region `us-west-1`
- ECR repo: `266735827053.dkr.ecr.us-west-1.amazonaws.com/engagex`
- Default VPC: `vpc-0d095d54d92bf9d34`
- Public subnet used for builds: `subnet-0cffd27f3533622e6`

## 6. Known stale/misleading artifacts in this repo

- `.github/workflows/deploy.yaml` — builds and pushes to Docker Hub on push to `deploy`.
  Unused by production. Safe to ignore or remove.
- Anything referencing Elastic Beanstalk (`.ebextensions/`, `.platform/`) — no active EB
  environment exists. These files are dead weight unless EB is reinstated.
