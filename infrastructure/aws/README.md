# AWS-ready deployment handoff

Use the same Python image with different commands for FastAPI, the four `ledgerguard.mcp_server` entrypoints, Celery worker and beat. Run Alembic and checkpoint setup as a one-off migration task before rolling services. Set DEMO_MODE=false; provision tenant users and risk/approval policies through an administrative process. Never use seeded demo accounts.

1. Create ECR repositories and push immutable image digests from CI.
2. Create private RDS PostgreSQL and ElastiCache Redis, with TLS and backups.
3. Store database, broker, JWT, MCP/delegation and optional provider keys in Secrets Manager. Use separate IAM task roles and secret access per service where possible.
4. Create ECS Fargate services in private subnets. Service discovery names replace Compose MCP URLs. Only API/frontend tasks accept ALB traffic. MCP inbound security groups allow the worker/API inspection path only.
5. Mount EFS for versioned model artifacts or download approved artifacts from S3 before serving. Run training separately. The local artifact volume is not suitable for cross-host ECS tasks.
6. Use HTTPS ALB listeners and CloudWatch structured logs/alarms. Set appropriate deployment rollback, health grace periods and task draining.
7. Keep one beat scheduler active. Scale worker concurrency conservatively; database advisory/row locks and unique executions preserve financial effects under re-delivery.
8. Add external immutable audit retention and least-privilege database grants. Validate restore procedures and tenant isolation with the deployed identities.

`task-definition.example.json` is an input template, not a pre-provisioned deployment. Replace every uppercase resource placeholder and secret ARN. No Terraform apply, AWS write or paid resource creation is performed by this repository.

Official references: [ECS Fargate task definitions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definitions.html), [ECS Secrets Manager integration](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html), [ECS service discovery](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-discovery.html).
