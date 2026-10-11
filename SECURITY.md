# Local use and credentials

The web service binds to 127.0.0.1. Each person should use their own OS account, data directory and remote API credentials. Do not expose this service to a LAN or public internet; it is a personal desktop workbench, not a multi-user hosted service.

API keys are supplied through hidden CLI input or password fields. Optional Windows DPAPI storage is tied to the same Windows account. Keys are not displayed again. Settings contain environment-variable names rather than plaintext keys. Paper excerpts and questions are sent to the remote provider explicitly configured by the user.

Downloaded research code is statically scanned without import. Explicit test calls run trusted reviewed code in a subprocess. Model-package blocking is a scope guard, not a general security sandbox.

If a key is accidentally shared, revoke it with the provider. Do not post keys, personal documents or unredacted private results in GitHub issues.
