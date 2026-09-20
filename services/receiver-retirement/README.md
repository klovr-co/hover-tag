# Hosted receiver retirement

This directory is a one-time Cloudflare cleanup deployment for the retired
`tag-offline-receiver` Worker. It deliberately retains the original `v1`
migration and applies `v2` to permanently delete the `TagReceiver` Durable
Object class and every stored registration.

After the revert reaches `main`, run the **Delete hosted receiver data** GitHub
workflow once and type `DELETE_DATA`. It applies the migration and checks the
live Worker settings for the absence of its Durable Object binding. After that
check succeeds, run **Retire hosted receiver Worker** and type `RETIRE` to
delete the remaining Worker and its secrets. Both workflows use the same
concurrency group, so deployment and deletion cannot race.

Users still running the hosted version should run `tag disconnect` before
updating, which removes their individual registration immediately.
