---
name: clay-plugin-eval
description: Set up Clay and run the GTM Bench experiment dataset. Use when a user wants Codex to create their Clay Vault, run selected GTM Bench tasks with the Clay plugin, and generate an experiment report.
---

# Clay plugin experiment

Set up one user's Clay access, run selected tasks with the Clay plugin, and return the verifier results.

## Defaults

- Dataset ID: `38bdc6cb-a255-44ff-91be-32a8170fe557` (fixed)
- Project ID: `129f3404-2f7f-4df8-9fcc-0164ffe92d73`
- Environment ID: `00b9da33-0bd2-440f-a740-6098200e5721`
- Harness: `codex`
- Model: `gpt-5.6-sol`

Use the default project, environment, harness, and model unless the user provides replacements. Ask for source task IDs, and run the entire dataset only when the user explicitly requests it.

After resolving overrides, use these shell variables in one persistent terminal session:

```bash
NEOSIGMA_PROJECT_ID="129f3404-2f7f-4df8-9fcc-0164ffe92d73"
NEOSIGMA_ENVIRONMENT_ID="00b9da33-0bd2-440f-a740-6098200e5721"
NEOSIGMA_DATASET_ID="38bdc6cb-a255-44ff-91be-32a8170fe557"
NEOSIGMA_HARNESS="codex"
NEOSIGMA_MODEL="gpt-5.6-sol"
```

## Set up Clay and its Vault

1. Confirm `neosigma` and `jq` are available and `NEOSIGMA_API_KEY` is set. If
   the CLI is missing or unauthenticated, follow
   [NeoSigma CLI getting started](https://docs.neosigma.ai/cli/getting-started).
   Never print the key.

2. If the Clay plugin is not installed, follow the official
   [Clay setup](https://github.com/clay-run/agent-plugins/blob/main/GETTING_STARTED.md):

   ```bash
   codex plugin marketplace add clay-run/agent-plugins
   ```

   Ask the user to install **Clay** from **Plugins**, then run `clay:setup`. If
   the new plugin is not visible yet, restart Codex and resume this skill. If
   `clay` is already available and current, do not reinstall it.

3. Resolve the selected project and its workspace:

   ```bash
   PROJECT_JSON="$(neosigma projects get --project-id "$NEOSIGMA_PROJECT_ID")"
   NEOSIGMA_WORKSPACE_ID="$(printf '%s' "$PROJECT_JSON" | jq -r '.project.workspace_id')"
   WORKSPACE_JSON="$(neosigma workspaces get --workspace-id "$NEOSIGMA_WORKSPACE_ID")"
   ```

   Before continuing, show the user the project name and ID and the workspace
   name and ID. Let the user replace the project or environment at this point.

4. Create an isolated Clay login for this user's Vault:

   ```bash
   CLAY_EVAL_CONFIG_HOME="$(mktemp -d)"
   CLAY_CONFIG_HOME="$CLAY_EVAL_CONFIG_HOME" clay login --device
   CLAY_CONFIG_HOME="$CLAY_EVAL_CONFIG_HOME" clay whoami
   ```

   The user completes the Clay sign-in. Do not expose or copy the credential
   anywhere except the Vault import.

5. Create the Vault in the resolved workspace and import the isolated login:

   ```bash
   NEOSIGMA_VAULT_ID="$(
     neosigma vaults create \
       --workspace-id "$NEOSIGMA_WORKSPACE_ID" \
       --display-name "Clay plugin GTM Bench experiment" |
     jq -r '.vault.id'
   )"

   neosigma vaults credentials import \
     --vault-id "$NEOSIGMA_VAULT_ID" \
     --format clay_cli_config \
     --config-json "$(jq -c . "$CLAY_EVAL_CONFIG_HOME/clay/config.json")"
   ```

   Delete only the temporary directory created in step 4 after the import
   succeeds. Do not create a second Vault when resuming the same run.

## Run and report

1. Run a selected task first with `--dry-run`. Repeat `--task` for more tasks, or
   omit it only when the user requested the full dataset.

   ```bash
   neosigma experiments run \
     --dataset "$NEOSIGMA_DATASET_ID" \
     --task "<source-task-id>" \
     --harness "$NEOSIGMA_HARNESS" \
     --model "$NEOSIGMA_MODEL" \
     --plugin clay \
     --vault-id "$NEOSIGMA_VAULT_ID" \
     --project "$NEOSIGMA_PROJECT_ID" \
     --environment "$NEOSIGMA_ENVIRONMENT_ID" \
     --attempts 1 \
     --max-concurrency 2 \
     --dry-run
   ```

   Repeat `--vault-id` if the plugin needs more than one Vault. After validation
   succeeds, run the same command without `--dry-run`.

2. Read `experiment.id` from the response into `NEOSIGMA_EXPERIMENT_ID`, then
   poll `neosigma experiments show "$NEOSIGMA_EXPERIMENT_ID"` until
   completion or failure. Do not start a replacement experiment automatically.

3. Save the detailed results and create the report:

   ```bash
   neosigma experiments results "$NEOSIGMA_EXPERIMENT_ID" --json \
     > clay-plugin-results.json

   neosigma experiments report "$NEOSIGMA_EXPERIMENT_ID" \
     --html ./clay-plugin-report.html
   ```

Report the selected project and workspace, experiment ID, status, named rewards,
failed trials, trace session IDs, and both output paths.
