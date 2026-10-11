# agent-hangar-cli

`hangar` — command-line client for [Agent Hangar](../README.md).

```bash
pip install ./cli
hangar login http://localhost:8090 --token <admin key>
hangar agents ls
hangar apply -f examples/agents.yaml     # GitOps: skills, MCP servers and agents from a file
hangar ship doc-qa                       # test in stage, promote to prod if green
hangar chat doc-qa "What is the hotel limit?"
hangar jobs run code-fixer "Fix the failing test" --follow
hangar keys create librechat --scope invoke --agent doc-qa
hangar test-drive                        # the whole path (team, Digital employee, task) with the test model
```

Credentials come from `HANGAR_URL` / `HANGAR_TOKEN` or from `~/.config/hangar/config.json` (written by `hangar login`,
readable only by you). See [docs/cli.md](../docs/cli.md) for every command.
