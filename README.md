# Course Bot · Online Learning Progress Assistant

English · [简体中文](./README.zh.md)

A self-hostable, containerized tool that advances your progress on an online
learning platform by reporting progress at a pace that simulates normal study.
Supports **three content types — workshops, courses, and micro-lessons** — with
cover-card UI, batch learning, and real progress verification.

> ⚠️ **For personal learning and technical research only.** Before downloading
> or using this software, read the [Disclaimer](#disclaimer) and the
> [LICENSE](./LICENSE) in the repository root.

---

## ✨ Features

- **Three content types**: workshops (with auto-enrollment), courses, and
  micro-lessons, presented in a unified cover-card interface.
- **Automatic progress**: progress reporting that mimics a realistic pace, with
  1x / 2x modes (2x is the safe ceiling accepted by the platform).
- **Batch learning**: one click to "learn the next 10 lessons" — no manual
  clicking through each one.
- **Real progress verification**: the platform's acknowledgement is the source
  of truth; items are never marked complete unless the platform confirms them.
- **Three-column dashboard**: in progress / up next / completed, sorted
  automatically.
- **Self-hosted**: one-command Docker Compose deployment; your data stays on
  your own machine.
- **Credential safety**: your username and password are used only in memory for
  the current login — never written to config files or printed in logs.

## 🖼️ Screenshots

| Dashboard | Learning Plaza |
|---|---|
| ![Dashboard](./docs/v322-dashboard.jpg) | ![Learning Plaza](./docs/v322-plazas.jpg) |

| Announcement | Auto-fill | Plaza detail |
|---|---|---|
| ![Announcement](./docs/v336-announcement.jpg) | ![Auto-fill](./docs/v336-autofill.jpg) | ![Plaza detail](./docs/v336-square.jpg) |

## 🧱 Tech Stack

- Backend: Python · FastAPI · SQLAlchemy (async) · SQLite
- Login: Playwright (in a separate login-worker container)
- Frontend: server-rendered pages + vanilla JavaScript
- Deployment: Docker · Docker Compose

---

## 🚀 Quick Start (pull the prebuilt image — recommended)

> 🔒 **Access required — private images.** The prebuilt images are hosted in a
> **private** GHCR organization to prevent abuse. They cannot be pulled
> anonymously.
>
> **How to get access:**
> 1. Have a GitHub account (free).
> 2. Send your **GitHub username** to the author via the official contact
>    channel (community group / email in your invitation). Access is granted
>    manually to verified users.
> 3. Log in once on your machine:
>    ```bash
>    echo YOUR_GITHUB_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
>    ```
>    Create the token at GitHub → Settings → Developer settings → Fine-grained
>    tokens, with **read-only `Packages: Read`** permission (no other scopes
>    needed).
>
> Access can be revoked at any time in case of abuse; the images remain free
> for personal study use.

> The **core engine is distributed only as a compiled binary (`.so`) inside the
> image. Source code for the engine is not provided and it cannot be built
> locally.** Please run the official image. Building from the public repository
> source will **omit the engine and will not run**.

### 1. Requirements

- Docker and Docker Compose installed (x86_64 / amd64).

### 2. Configuration

Copy the `docker-compose.yml` below and create a `.env` file in the same
directory:

```dotenv
CCB_USERNAME=your_username
CCB_PASSWORD=your_password
WEB_PORT=8090
```

### 3. Start

```bash
docker compose up -d
```

Open `http://localhost:8090` in your browser and enter your credentials on the
login page. All data and cache are stored locally in the `./data` volume and
never pass through any third-party server.

---

## 🔐 Security & Privacy

- Your username and password are passed only in memory for the login request —
  **never persisted to disk, never logged** — and are gone when the service
  restarts.
- All learning data is stored in your own self-hosted database; it never passes
  through the author's or any third-party server.
- `.env` and `data/` should never be committed to any code repository.

---

## 📦 Source Code & Engine

This project is released as **"visible source + closed-source engine
binary"**:

| Part | Form |
|---|---|
| Routes, data models, front-end pages, scheduling/orchestration, release tooling | plain source, viewable and auditable |
| Core engine — platform API wrappers, progress algorithms, request signing | compiled `*.so`, distributed only inside the image |

For the boundary between source and engine and how images are distributed, see
[LICENSE](./LICENSE); the build and desensitization tooling lives under
[`release/`](./release).

---

## ⚖️ License

This software is **not open-source software**. It is released under a
proprietary, all-rights-reserved license (see [LICENSE](./LICENSE)). You may
view the source and pull the image to self-host, but you **may not copy,
redistribute, mirror the image, use it commercially, republish it under a
different name, or remove attribution**.

---

## Disclaimer

1. This project is intended **solely for personal learning and technical
   research**, exploring web automation, containerization, and front-end/back-end
   engineering practices.
2. Whether and how to use it is entirely **the user's own decision**. Users are
   responsible for understanding and complying with the rules of their
   organization and of the platform, and bear all risks and consequences
   themselves.
3. The author is not liable for any direct or indirect consequences arising from
   anyone's use of this tool, including but not limited to account anomalies,
   erased learning records, or disciplinary/administrative action.
4. Please use it **reasonably and in moderation**; do not disrupt the normal
   operation of the platform, and do not use this tool for any non-compliant or
   illegal activity.
5. This project is **not affiliated with** any organization or platform, nor
   endorsed or authorized by them; names used herein refer only to the
   corresponding technical services.
6. Downloading, building, or starting to use this software constitutes that you
   have read, understood, and agreed to the full contents of this disclaimer and
   the LICENSE.
