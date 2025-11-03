# ⌚ 20-min Quickstart
In this article you will understand how to deploy the Flight Blender backend / data processing engine. If you need a front end / display you will need to install [Flight Spotlight](https://flightspotlight.com) (which communicates with Flight Blender via the API) and finally for production we also recommend that you use [Flight Passport](https://www.github.com/openskies-sh/flight_passport) authorization server for endpoint security.

## Who is this for?
This guide is mainly for technical engineers within organizations interested in testing and standing up UTM capability. It is recommended that you are familiar with basic Docker, OAUTH/Bearer Tokens. The server is written in Django/Python if you want to use/run the built-in data. However, since it is all API-based, you can use any tools/languages that you are familiar with to communicate with the server.

## Introduction and objectives

This quick start is for local development/testing only. For a more detailed "Production" instance, see the currently under development [Production Deployment](https://github.com/openutm/deployment/blob/main/oauth_infrastructure.md) document. The main difference between local development and production is that for production, you will need a full-fledged OAUTH server like [Flight Passport](https://github.com/openutm/flight_passport) or others. For this quickstart, we will use the simple authentication/token generation mechanism that requires no additional server setup. In this quickstart, we will:

1. Create a .env file
2. Use Docker Compose to run the Flight Blender server
3. Use the importers to submit some flight information
4. Finally, query the flight data using the API via a tool like Postman.

### 1. Create .env File

For this quick start, we will use the [sample .env](https://github.com/openutm/flight-blender/blob/master/deployment_support/.env.local) file. You can copy the file to create a new .env file. We will go over the details of the file below.

| Variable Key | Data Type | Description |
|--------------|--------------|:-----:|
| SECRET_KEY | string | This is used in Django. It is recommended that you use a long SECRET Key as a string here. |
| IS_DEBUG | integer | Set this as 1 if you are using it locally. |
| BYPASS_AUTH_TOKEN_VERIFICATION | integer | Set this as 1 if you are using it locally or using NoAuth or Dummy tokens. **NOTE** Please remove this field totally for any production deployments, as it will bypass token verification and will be a security risk. |
| ALLOWED_HOSTS | string | This is used in Django. It is recommended that if you are not using IS_DEBUG above, then this needs to be set as the domain name. If you are using IS_DEBUG above, then the system automatically allows all hosts. |
| REDIS_HOST | string | Flight Blender uses Redis as the backend. You can use localhost if you are running Redis locally. |
| REDIS_PORT | integer | Normally Redis runs at port 6379. You can set it here. If you don't set up the REDIS Host and Port, Flight Blender will use the default values. |
| REDIS_PASSWORD | string | In production, the Redis instance is password protected. Set the password here. See redis.conf for more information. |
| REDIS_BROKER_URL | string | Flight Blender has background jobs controlled via Redis. You can set up the Broker URL here. |
| HEARTBEAT_RATE_SECS | integer | Generally set it to 1 or 2 seconds. This is used when querying data externally to other USSPs. |
| DATABASE_URL | string | A full database URL with username and password as necessary. You can review various database [URL schema](https://github.com/jazzband/dj-database-url#url-schema). |

If you are working in stand-alone mode, recommended initially, the above environment file should work. If you want to engage with a DSS and inter-operate with other USSes, then you will need additional variables below.

| Variable Key | Data Type | Description |
|--------------|--------------|:-----:|
| USSP_NETWORK_ENABLED | int | Set it as 0 for standalone mode. Set it as 1 for interacting with an ASTM compliant DSS system. |
| DSS_SELF_AUDIENCE | string | This is the domain name of the lender instance. You can set it as localhost or development/testing. |
| AUTH_DSS_CLIENT_ID | string | (optional) Sometimes authorities will provide special tokens for accessing the DSS. If you are using it locally via `/build/dev/run_locally.sh` via the InterUSS/DSS repository, you can just use a random long string. |
| AUTH_DSS_CLIENT_SECRET | string | (optional) Similar to above, sometimes authorities provide. |
| DSS_BASE_URL | string | Set the URL for DSS. If you are using it, it can be something like `http://host.docker.internal:8082/` if you are using the InterUSS/DSS build locally stack. |
| POSTGRES_USER | string | Set the user for the Flight Blender Database. |
| POSTGRES_PASSWORD | string | Set a strong password for accessing PG in Docker. |
| POSTGRES_DB | string | You can name an appropriate name. See the sample file. |
| POSTGRES_HOST | string | You can name an appropriate name. See the sample file. |
| PGDATA | string | This is where the data is stored. You can use `/var/lib/postgresql/data/pgdata` here. |
| FLIGHTBLENDER_FQDN | string | This is the domain name of a Flight Blender deployment, e.g., `https://beta.flightblender.com`. |

### 2. Use Docker Compose to stand up Flight Blender
Once you have created and saved the .env file, you can then use the [docker-compose.yaml](../docker-compose.yml) file to start the instance. Just run `docker compose up` and a running instance of Flight Blender will be available.

#### Running Flight Blender
You can run Flight Blender by running `docker compose up` and then go to `http://localhost:8000`. You should see the Flight Blender Logo and a link to the API and Ping documentation. Congratulations 🎉 we now have a running version of the system!

### 3. Upload some flight information
Next, we can now upload flight data. Flight Blender has an extensive API, and you can review it. Any data uploaded or downloaded is done via the API. The [importers](../importers/) directory has a set of scripts that help you with uploading data/flight tracks. For this quickstart, we will use the [import_flight_json_flight_blender_local.py](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/import_flight_json_flight_blender_local.py) script here. You can see the rest of the scripts there to understand how it works.

You will have to set up an environment like Anaconda or a similar software package and install dependencies via something like `pip install -r requirements.txt`. Then you can run the import script via `python import_flight_json_flight_blender_local.py`. This will send some observations to the `/set_air_traffic` POST endpoint. This script will send an observation and then wait for 10 seconds and send another one. All of this requires Python.

### 4. Use Postman to query the API
While the script is running, you can install Postman, which should help us query the API. You can import the [Postman Collection](../api/flight_blender_api.postman_collection.json) prior. You will also need a "NoAuth" Bearer JWT token that you can generate by using the [get_access_token.py](https://github.com/openutm/verification/blob/main/src/openutm_verification/importers/get_access_token.py) script. You should have a scope of `blender.read` and an audience of `testflight.flightblender.com`. We will use this token to go to the Postman collection > Flight Feed Operations > Get airtraffic observations. You should be able to see the output of the flight feed as a response!

## Frequently asked Questions (FAQs)

**Q: Docker compose errors out because of Postgres not launching**
A: Check the existing Postgres port and/or shut down Postgres if you have it. Flight Blender Docker uses the default SQL ports.

**Q: Where do I point my tools for Remote ID/Strategic Deconfliction APIs?**
A: Check the [API Specification](http://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/openutm/flight-blender/master/api/flight-blender-1.0.0-resolved.yaml) to see the appropriate endpoints and/or download the [Postman Collection](../api/flight_blender_api.postman_collection.json) to see the endpoints.

**Q: Is there a guide on how to configure Flight Passport to be used with Flight Blender + Spotlight?**
A: Yes, there is a small [OAUTH Infrastructure](https://github.com/openutm/deployment/blob/main/oauth_infrastructure.md) document.
