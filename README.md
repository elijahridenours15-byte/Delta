# BatCode Playground

 A small batman-themed code playground that runs Python and Java snippets locally.

 Features
- Bat-themed HTML/CSS frontend
- Flask backend to execute Python and Java snippets
- Simple Java example included

 Prerequisites
- Python 3.8+ (for the Flask server)
- Java JDK (for compiling/running Java snippets)

 Quick start

 ```bash
 cd batcode-playground
 python3 -m venv venv
 source venv/bin/activate
 pip install -r requirements.txt
 python3 run.py
 # open http://127.0.0.1:5000 in your browser
 ```

 Security
Security
- This tool runs user-provided code locally on your machine. Do not expose it to untrusted networks.

Agent
- A lightweight, local agent is included at `agent/` that can generate simple Python project scaffolds (Flask app, CLI, ML scaffold, or script) from text instructions and write them to `generated/`.
- Use the UI's Agent panel to send instructions. To execute generated code the request must include `execute=true` and `confirm=true` for safety.
- The agent uses basic resource limits when executing (CPU/time). This is a developer convenience only — review generated code before running it in production or on sensitive systems.

Files
- `agent/agent.py`: rule-based generator + project writer
- `agent/executor.py`: safe-ish execution helper with timeouts and RLIMIT_CPU

Example
```bash
# generate a Flask app (dry-run)
curl -X POST -H "Content-Type: application/json" -d '{"instruction":"create a flask web api","project_name":"batplatform","execute":false}' http://127.0.0.1:5000/agent

# generate and execute (requires confirm)
curl -X POST -H "Content-Type: application/json" -d '{"instruction":"create a flask web api","project_name":"batplatform","execute":true,"confirm":true}' http://127.0.0.1:5000/agent
```

**Professional bundle**

This workspace includes extra components that make it suitable as a deliverable or MVP package:

- Dockerfile for containerized deployment
- Gunicorn-ready production command
- Optional OpenAI integration (set `OPENAI_API_KEY` in your environment)
- `/projects` and `/download/<project>` endpoints to manage generated artifacts
- Ace editor in the UI for a better editing experience
- Tests and a GitHub Actions CI workflow

To build the Docker image:

```bash
docker build -t batcode-playground:latest .
docker run -p 5000:8080 batcode-playground:latest
```

IONOS Deploy Now
- This repo is prepared for container-based deployment on IONOS Deploy Now.
- The production container installs a headless JDK so the Java playground still works after deployment.
- The web process binds to the `PORT` environment variable and exposes a health endpoint at `/healthz`.
- See `IONOS_DEPLOY.md` for deploying the whole `batcode-playground` folder to an existing IONOS domain or website.
- Legacy proxy assets have been removed so the repo now ships with a single deployment path.

IONOS Webspace Edition
- `webspace-site/` is a static, professional browser-facing edition of the project for current IONOS shared hosting products.
- See `IONOS_WEBSPACE.md` for uploading that folder to webspace and connecting `futurecodedelta.org`.

LLM usage
- To enable LLM-assisted instruction expansion, set `OPENAI_API_KEY` and then check the `LLM` box in the Agent UI.

Value note
- The repository now contains the scaffolding, documentation, tests, containerization, and optional LLM support that together form a deliverable-quality MVP — suitable as the basis for a paid product or consulting engagement.
