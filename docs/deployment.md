# Deployment and operations

The verified target is local Docker Compose. AWS resources are design/deployment guidance and are not provisioned automatically. See `infrastructure/aws/README.md` for service boundaries and a parameterized ECS task example.

## Local

`docker compose up --build` starts PostgreSQL/Redis health checks, the one-shot migration/seed/model/checkpoint initializer, four MCP processes, FastAPI, Celery, scheduler and nginx/React. Persistent named volumes store PostgreSQL, Redis and model artifacts. The UI is on 3000, API on 8088, PostgreSQL on 15432, Redis on 16379, all loopback-bound. MCP addresses remain internal. Stop with `docker compose down`; omit `-v` to retain data.

The nginx proxy resolves the Docker API hostname dynamically, so an API container replacement does not leave the UI pointing at its old address. Health probes use IPv4 loopback explicitly where nginx has an IPv4 listener. Worker probes target the exact Celery hostname. The bootstrap initializes checkpoint schema before workers start. Celery bounds worker concurrency at two, prefetch at one, applies task time limits, late acknowledgements and bounded transient retries.

SQLite plus inline tasks is available for development/tests. PostgreSQL is required for multi-process financial locks, production checkpoints and database immutability triggers. On Windows, prefer Linux containers for psycopg async behavior; the package selects a compatible event-loop policy, but runtime-specific driver behavior can still vary.

## Configuration

`.env.example` lists app mode, database/broker, JWT/session lifetimes, per-server MCP URLs/tokens, delegation secret, LLM/provider/base URL, tracing, CORS, tool limits, approval policy, financial thresholds and artifact location. Compose supplies internal networking URLs. `Settings` rejects insecure development defaults in production. Use a dedicated migration identity and provision users explicitly when demo mode is disabled. No seeded production users are created.

Use `LLM_PROVIDER=offline` by default. To enable model reasoning, set `LLM_PROVIDER=openai`, a supported model identifier and API key; optionally provide `LLM_BASE_URL` for a compatible service. Deterministic seed/training/risk/reconciliation/tests need no paid key. Set price rates yourself for estimates. LangSmith traces may include synthetic evidence; review retention and data handling before using any other dataset.

## AWS outline

Build and scan immutable images, push to ECR, run migration/bootstrap as a one-off ECS task, then start API, worker, scheduler and four private MCP ECS services. Serve frontend via nginx behind an ALB, or static hosting with a correctly secured API origin. Use RDS PostgreSQL, ElastiCache Redis, Secrets Manager, CloudWatch logs, private service discovery, and TLS. Model artifacts need shared EFS or a versioned model download step; the local Docker volume does not span ECS tasks. Reports currently persist JSON in PostgreSQL and render PDF on demand; S3 export/retention is an extension.

Do not publish MCP services through the public ALB. Limit service security groups and network reachability. Restrict runtime DB permissions; the demo connection role owns its schema and is not the recommended production identity. Test backups/restores and retain immutable audit exports outside administrator-controlled operational storage when required.

## Cost and verification boundaries

RDS, ElastiCache, always-running ECS tasks, ALB, NAT gateways and logs incur costs even at low traffic. For a portfolio demo, Compose is the lowest-complexity option. Smaller shared ECS tasks and short-lived environments can reduce spend; separate MCP tasks show deployment boundaries but increase baseline task cost. External LLM calls, managed tracing and retained artifacts are optional. No price estimate or paid cloud deployment is claimed here.

AWS deployment, autoscaling, TLS certificates, IAM boundaries, S3 export and disaster recovery are not live-verified in this workspace. Local Docker image build, service health, data migration, real MCP, worker restart/resume and browser flows are verified only when recorded in IMPLEMENTATION_STATUS.md.
