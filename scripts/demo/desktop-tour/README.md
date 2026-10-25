# Desktop tour: the Hangar desktop app connected to Agent Hangar

These files regenerate the README's desktop video (`docs/assets/hangar-desktop.mp4`) and the screenshots in
`docs/assets/desktop/`.

## What was recorded

The run happened on 2026-10-25 against a development instance of the central (port 8091, SQLite, see
"Desenvolvimento" in `docs/app-hangar.md`), never against a real install. People and companies are fictional:
the company is renamed *Nimbus Logística*, the member *Marina Costa* and the admin *Rafael Lima*.

- `upstream.py` is a fake Anthropic-compatible provider on `127.0.0.1:8792`. It lists two models and answers every
  `/v1/messages` with the same scripted reply, streamed. An admin created the LLM connection `claude-empresa`
  pointing at it.
- `capture.spec.ts` is a Playwright spec for the Hangar desktop repository: it reuses that repo's e2e fixtures
  (`tests/e2e/fixtures.ts`) to launch the real app. The admin's console screens are taken in Edge. The app
  connects as the member, the consent screen is approved in the browser, and a session runs one turn on the
  synced token model through the central's LLM proxy.
- `make_video.py` adds the title, benefits and install cards and the captions, and writes the MP4 and the README
  stills.

## Running it again

```bash
python upstream.py 8792
# in the Hangar desktop repository, with the dev instance on 8091 and its test users in the environment
# (HANGAR_PLATFORM_URL, HANGAR_PLATFORM_USER/PASSWORD for the admin, HANGAR_PLATFORM_MEMBER_USER/PASSWORD):
cp <this folder>/capture.spec.ts tests/e2e/zz-video-capture.spec.ts
SHOTS_DIR=<this folder>/shots npx playwright test tests/e2e/zz-video-capture.spec.ts
rm tests/e2e/zz-video-capture.spec.ts
# back here
docker run --rm -v "$PWD:/demo" mcr.microsoft.com/playwright/python:v1.63.0-noble \
  sh -c "pip install -q pillow numpy imageio-ffmpeg && python /demo/make_video.py"
```

The test users' passwords are secrets of the dev instance: never commit them.
