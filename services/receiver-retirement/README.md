# Hosted receiver retirement

This directory is a one-time Cloudflare cleanup deployment for the retired
`tag-offline-receiver` Worker. It deliberately retains the original `v1`
migration and applies `v2` to permanently delete the `TagReceiver` Durable
Object class and every stored registration.

The CI workflow deploys this configuration after the revert reaches `main` and
checks the live Worker settings for the absence of its Durable Object binding.
After that check succeeds, run the **Retire hosted receiver Worker** GitHub
workflow and type `RETIRE` to delete the remaining Worker and its secrets.

Users still running the hosted version should run `tag disconnect` before
updating, which removes their individual registration immediately.
