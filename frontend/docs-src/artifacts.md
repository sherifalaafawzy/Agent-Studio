---
title: Artifacts
section: Work
order: 14
summary: Files and results your agents produce, kept safely so you can find, preview and download them later.
tags: artifacts, files, reports, storage, s3, download, retention, secret scan
routes: /artifacts/
---
**Artifacts** are the files and results agents produce: reports, charts, data, code, models. Agent Studio keeps a copy so you can find, preview and download them even after the agent's workspace changes.

![Artifacts: files and results your agents made](shot:artifacts)

## How things become artifacts

- When an agent finishes a turn, files that appeared in its workspace are kept.
- An agent can keep one on purpose with its `save_artifact` tool.
- You can keep any answer with the **Keep** button in chat.
- Canvases are kept too. See [Canvas](doc:canvas).

## Browse and preview

The page searches names, tags and file contents, and filters by kind. It previews tables, JSON, charts, audio and video. HTML, XML and scripts are shown as **text**, never run. Files are served with protections that stop the browser guessing types or running code.

## Storage

Settings, Artifacts (or the Storage and rules area on this page) chooses where copies live: a folder on this PC (always on), Amazon S3, S3-compatible services (MinIO, Cloudflare R2, DigitalOcean Spaces and similar), Google Cloud Storage, Azure Blob (a SAS token) and WebDAV. Several can be on at once, and rules say which kinds of file go where; the first matching rule wins. Credentials are write-only: you can enter them but never read them back.

> **Only the folder on this PC has been used for real.** The cloud stores are tested against in-memory fakes of their APIs. Try one with a throw-away bucket first.

## Safety

- A **secret scan** runs before a copy leaves your PC. The default removes secret-looking values from text; the alternative refuses to send that file. The copy on this PC is never changed. Binary files cannot be scanned.
- **Retention** (days) deletes old artifacts from every storage.
- The AI-BOM lists only counts and storage kinds, never names or content. See [AI-BOM and discovery](doc:ai-bom).
