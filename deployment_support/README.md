# ⌚ 20-min Quickstart

In this article you will understand how to deploy the Flight Blender backend / data processing engine. If you need a front end / display you will need to install [Flight Spotlight](https://flightspotlight.com) (which communicates with Flight Blender via the API) and finally for production we also recommend that you use [Flight Passport](https://www.github.com/openskies-sh/flight_passport) authorization server for endpoint security.

## Who is this for?

This guide is mainly for technical engineers within organizations interested in testing and standing up UTM capability. It is recommended that you are familiar with basic Docker, OAUTH/Bearer Tokens. The server is written in FastAPI/Python if you want to use/run the built-in data. However, since it is all API-based, you can use any tools/languages that you are familiar with to communicate with the server.

## Introduction and objectives

This quick start is for local development/testing only. For a more detailed "Production" instance, see [Production Deployment](https://github.com/openutm/deployment/) repository. The main difference between local development and production is that for production, you will need a full-fledged OAUTH server like [Flight Passport](https://github.com/openutm/flight_passport) or others. For this quickstart, we will use the simple authentication/token generation mechanism that requires no additional server setup. In this quickstart, we will:

1. Create a .env file
2. Use Docker Compose to run the Flight Blender server
3. Use the importers to submit some flight information
4. Finally, query the flight data using the API via a tool like Postman.

### 1. Create .env File

For this quick start, we will use the [sample .env](https://github.com/openutm/flight-blender/blob/master/deployment_support/.env.local) file. You can copy the file to create a new .env file. We will go over the details of the file below.

| Variable Key | Data Type | Description |
|--------------|--------------|:-----:|
| SECRET_KEY | string | This is used for JWT signing. It is recommended that you use a long SECRET Key as a string here. |
| IS_DEBUG | integer | Set this as 1 if you are using it locally. |
| BYPASS_AUTH_TOKEN_VERIFICATION | integer | Set this as 1 if you are using it locally or using NoAuth or Dummy tokens. **NOTE** Please remove this field totally for any production deployments, as it will bypass token verification and will be a security risk. |
| ALLOWED_HOSTS | string | Comma-separated list of allowed hostnames. If you are not using IS_DEBUG above, then this needs to be set as the domain name. If you are using IS_DEBUG above, then the system automatically allows all hosts. |
| REDIS_HOST | string | Flight Blender uses Redis as the backend. You can use localhost if you are running Redis locally. |
| REDIS_PORT | integer | Normally Redis runs at port 6379. You can set it here. If you don't set up the REDIS Host and Port, Flight Blender will use the default values. |
| REDIS_PASSWORD | string | In production, the Redis instance is password protected. Set the password here. See redis.conf for more information. |
| REDIS_BROKER_URL | string | Flight Blender has background jobs controlled via Redis. You can set up the Broker URL here. |
| HEARTBEAT_RATE_SECS | integer | Generally set it to 1 or 2 seconds. This is used when querying data externally to other USSPs. |
| DATABASE_URL | string | A full database URL with username and password as necessary. You can review various database [URL schema](https://github.com/jazzband/dj-database-url#url-schema). |
| POSTGRES_USER | string | (Docker Compose) Set the user for the Flight Blender Database. |
| POSTGRES_PASSWORD | string | (Docker Compose) Set a strong password for accessing PG in Docker. |
| POSTGRES_DB | string | (Docker Compose) You can name an appropriate name. See the sample file. |
| POSTGRES_HOST | string | (Docker Compose) You can name an appropriate name. See the sample file. |
| PGDATA | string | (Docker Compose) This is where the data is stored. You can use `/var/lib/postgresql/data/pgdata` here. |

If you are working in stand-alone mode, recommended initially, the above environment file should work. If you want to engage with a DSS and inter-operate with other USSes, then you will need additional variables below.

| Variable Key | Data Type | Description |
|--------------|--------------|:-----:|
| USSP_NETWORK_ENABLED | int | Set it as 0 for standalone mode. Set it as 1 for interacting with an ASTM compliant DSS system. |
| AUTO_SUBMIT_TO_DSS | int | (optional, default `1`) Set it to `0` to prevent flight declarations from being automatically submitted to the DSS upon creation. When disabled, declarations remain in state `0` (`ProcessingNotSubmittedToDss`) until manually promoted via the `POST /flight_declaration_ops/flight_declaration/<uuid>/submit_to_dss` endpoint. Useful in tactical workflows where operators create multiple candidate declarations and choose one to submit. |
| DSS_SELF_AUDIENCE | string | This is the domain name of the lender instance. You can set it as localhost or development/testing. |
| AUTH_DSS_CLIENT_ID | string | (optional) Sometimes authorities will provide special tokens for accessing the DSS. If you are using it locally via `/build/dev/run_locally.sh` via the InterUSS/DSS repository, you can just use a random long string. |
| AUTH_DSS_CLIENT_SECRET | string | (optional) Similar to above, sometimes authorities provide. |
| DSS_BASE_URL | string | Set the URL for DSS. If you are using it, it can be something like `http://host.docker.internal:8082/` if you are using the InterUSS/DSS build locally stack. |
| FLIGHTBLENDER_FQDN | string | This is the domain name of a Flight Blender deployment, e.g., `https://beta.flightblender.com`. |

### 2. Run Flight Blender

You have two options: **Docker Compose** (recommended for first-time setup) or **running locally** (useful for development).

#### Option A: Docker Compose (recommended)

After creating and saving the .env file, you can utilize the [docker-compose-dev.yaml](../docker-compose-dev.yml) file to launch the instance. Simply execute `docker build . -t openutm/flight-blender-dev` to create the image, followed by `docker compose up` to make a running Flight Blender instance accessible.

You can run Flight Blender by running `docker compose up` and then go to `http://localhost:8000`. You should see the Flight Blender Logo and a link to the API and Ping documentation. Congratulations — we now have a running version of the system!

#### Option B: Running locally (without Docker)

##### Prerequisites

- **Python 3.12** (`python3 --version` should show 3.12.x)
- **PostgreSQL** (14+) running locally, or **SQLite** for quick testing
- **Redis** running locally (default port 6379)
- **uv** package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))

##### 1. Install dependencies

```bash
cd flight-blender
uv sync
```

##### 2. Create your .env file

Copy the sample file and edit it for local hostnames:

```bash
cp deployment_support/.env.local .env
```

Key changes for local (non-Docker) runs:

| Variable | Docker value | Local value |
|---|---|---|
| `REDIS_HOST` | `redis-blender` | `localhost` |
| `REDIS_BROKER_URL` | `redis://:blender_redis@redis-blender:6379` | `redis://localhost:6379/` |
| `DATABASE_URL` | `psql://...@myproject_db:5432/myproject_db` | see below |
| `POSTGRES_HOST` | `myproject_db` | `localhost` |

For **SQLite** (no PostgreSQL needed), set:

```
DATABASE_URL=sqlite:///./flight_blender.sqlite3
```

For **PostgreSQL**, set:

```
DATABASE_URL=postgresql://user:password@localhost:5432/flightblender
```

Make sure `BYPASS_AUTH_TOKEN_VERIFICATION=1` is set for local development.

##### 3. Apply database migrations

```bash
alembic upgrade head
```

This creates all required tables. On a fresh SQLite database this is all you need; for PostgreSQL make sure the database exists first (`createdb flightblender` or `CREATE DATABASE flightblender;`).

##### 4. Start the services

You need **three processes** running simultaneously. Open three terminals:

**Terminal 1 — API server:**

```bash
uvicorn flight_blender.asgi:application --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Celery worker** (processes background jobs):

```bash
celery --app=flight_blender worker --loglevel=info
```

**Terminal 3 — Celery beat** (schedules periodic tasks):

```bash
celery --app=flight_blender beat --loglevel=info --schedule=/tmp/celerybeat-schedule
```

The API is now at `http://localhost:8000`. You should see the Flight Blender logo and links to the API docs.

##### 5. Verify it works

```bash
curl http://localhost:8000/
```

You can also open `http://localhost:8000/docs` in a browser to see the interactive Swagger UI.

### 3. Upload some flight information

Next, we can now upload flight data. Flight Blender has an extensive API, and you can review it. Any data uploaded or downloaded is done via the API. We have a [verification](https://www.github.com/openutm/verification) repository that will help you with interacting with your system. It will submit flight declarations and other data in and out of Flight Blender.


### 4. Use Postman to query the API

While the script is running, you can install Postman, which should help us query the API. You can import the [Postman Collection](../api/flight_blender_api.postman_collection.json) prior. You will also need a "NoAuth" Bearer JWT token that you can generate by using the [get_access_token.py](https://github.com/openutm/verification/blob/main/src/openutm_verification/importers/get_access_token.py) script. You should have a scope of `blender.read` and an audience of `testflight.flightblender.com`. We will use this token to go to the Postman collection > Flight Feed Operations > Get airtraffic observations. You should be able to see the output of the flight feed as a response!

## Frequently asked Questions (FAQs)

**Q: Docker compose errors out because of Postgres not launching**
A: Check the existing Postgres port and/or shut down Postgres if you have it. Flight Blender Docker uses the default SQL ports.

**Q: Where do I point my tools for Remote ID/Strategic Deconfliction APIs?**
A: Check the [API Specification](http://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/openutm/flight-blender/master/api/flight-blender-1.0.0-resolved.yaml) to see the appropriate endpoints and/or download the [Postman Collection](../api/flight_blender_api.postman_collection.json) to see the endpoints.

**Q: Is there a guide on how to configure Flight Passport to be used with Flight Blender + Spotlight?**
A: Yes, there is a small [OAUTH Infrastructure](https://github.com/openutm/deployment/blob/main/oauth_infrastructure.md) document.
