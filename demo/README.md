# Project Pilot demo

`demo/sample-project` is a tiny app with a login bug and a non-responsive page.
Run the whole workflow with no AI account and no network:

    python scripts/demo_workflow.py

It drives the real Project Pilot API in a throw-away data folder: import the project, write a master prompt,
generate and approve a task plan, let the engine hand each task to you (a built-in "manual" account), paste
simulated AI answers (including file changes), review and approve the changes, and see checkpoints and the
HANDOFF.md package. In the real app you do the same in the browser, pasting answers from any AI website.
The sample's `.env` shows protection: a simulated AI tries to overwrite it and Project Pilot refuses.
