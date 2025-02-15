# ⌚ 20-min Quickstart
In this article you will understand how to deploy the Argon Server backend / data processing engine. If you need a front end / display you will need to install [Flight Spotlight](https://flightspotlight.com) (which communicates with Argon Server via the API) and finally for production we also recommend that you use [Flight Passport](https://www.github.com/openskies-sh/flight_passport) authorization server for endpoint security.

## Who is this for?
This guide is mainly for technical engineers within organizations who are interested in testing and standing up UTM capability. It is recommended that you are familiar with basic Docker, OAUTH / Bearer Tokens. The server is written in Django / Python if you want to use / run the in built in data. However, since it is all API based, you can use any tools / languages that you are familiar with to communicate with the server.

## Introduction and objectives

This quick start is for local development / testing only, for a more detailed "Production" instance see the currently under development [Production Deployment](oauth_infrastructure.md) document. The main difference between local development and production is that for production you will need a full fledged OAUTH server like [Flight Passport](https://github.com/openutm/flight_passport) or others. For this quickstart we will use the simple authentication / token generation mechanism that requires not additional server setup. In this quickstart, we will:

1. Create a .env file
2. Use Docker compose to run Argon Server server
3. Use the importers to submit some flight information
4. Finally query the flight data using the API via a tool like Postman.

### 1. Create .env File

For this quick start we will use the [sample .env](https://github.com/openutm/flight-blender/blob/master/deployment_support/.env.local) file. You can copy the file to create a new .env file, we will go over the details of the file below.

| Variable Key | Data Type | Description |
|--------------|--------------|:-----:|
| SECRET_KEY | string |This is used in Django, it is recommended that you use a long SECRET Key as string here |
| IS_DEBUG |integer | Set this as 1 if you are using it locally,  |
| BYPASS_AUTH_TOKEN_VERIFICATION |integer | Set this as 1 if you are using it locally or using NoAuth or Dummy tokens, **NOTE** Please remove this field totally for any production deployments, it will by pass token verification and will be a security risk |
| ALLOWED_HOSTS | string | This is used in Django, it is recommended that if you are not using IS_DEBUG above, then this needs to be set as a the domain name, if you are using IS_DEBUG above, then the system automatically allows all hosts|
| REDIS_HOST | string | Argon Server uses Redis as the backend, you can use localhost if you are running redis locally |
| REDIS_PORT | integer | Normally Redis runs at port 6379, you can set it here, if you dont setup the REDIS Host and Port, Argon Server will use the default values |
| REDIS_PASSWORD | string | In production the Redis instance is password protected, set the password here, see redis.conf for more information |
| REDIS_BROKER_URL | string | Argon Server has background jobs controlled via Redis, you can setup the Broker URL here |
| HEARTBEAT_RATE_SECS |integer | Generally set it to 1 or 2 seconds, this is used when querying data externally to other USSPs |
| DATABASE_URL |string | A full database url with username and password as necessary, you can review various database [url schema](https://github.com/jazzband/dj-database-url#url-schema) |

If you are working in stand-alone mode, recommended initially, the above environment file should work. If you want to engage with a DSS and inter-operate with other USSes then you will need additional variables below.

| Variable Key | Data Type | Description |
|--------------|--------------|:-----:|
| USSP_NETWORK_ENABLED |int | Set it as 0 for standalone mode set it as 1 for interacting with a ASTM compliant DSS system |
| DSS_SELF_AUDIENCE |string | This is the domain name of the lender instance you can set it as localhost or development / testing |
| AUTH_DSS_CLIENT_ID | string | (optional) Sometimes authorities will provide special tokens for accessing the DSS, if you are using it locally via `/build/dev/run_locally.sh` via the InterUSS /DSS repository, you can just use a random long string |
| AUTH_DSS_CLIENT_SECRET | string | (optional) Similar to above sometimes authorities provide  |
| DSS_BASE_URL | string | Set the URL for DSS if you are using it it can be something like `http://host.docker.internal:8082/` if you are using the InterUSS / DSS build locally stack. |
| POSTGRES_USER | string | Set the user for the Argon Server Database |
| POSTGRES_PASSWORD| string | Set a strong password for accessing PG in Docker |
| POSTGRES_DB | string| You can name a appropriate name, see the sample file |
| POSTGRES_HOST | string| You can name a appropriate name, see the sample file |
| PGDATA | string | This is where the data is stored, you can use `/var/lib/postgresql/data/pgdata` here |
| FLIGHTBLENDER_FQDN | string | This is the domain name of a Argon Server deployment e.g. `https://beta.flightblender.com` |

### 2. Use Docker Compose to stand up Argon Server
Once you have created and saved the .env file you can then use the [docker-compose.yaml](../docker-compose.yml) file to start the instance. Just run `docker compose up` and a running instance of Argon Server will be available.

#### Running Argon Server
You can run Argon Server by running `docker compose up` and then go to `http://localhost:8000`, you should see the Argon Server Logo and a link to the API and Ping documentation. Congratulations 🎉 we now have a running version of the system!

### 3. Upload some flight information
Next we can now upload flight data. Argon Server has a extensive API and you can review it, any data uploaded or downloaded is done via the API. The [importers](../importers/) directory has a set of scripts that help you with uploading data / flight tracks. For this quickstart, we will use the [import_flight_json_flight_blender_local.py](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/import_flight_json_flight_blender_local.py) script here, you can see the rest of the scripts there to understand how it works.

You will have to setup a environment like Anaconda or similar software package and install dependencies via something like `pip install -r requirements.txt` then you can run the import script via `python import_flight_json_flight_blender_local.py` this will send some observations to the `/set_air_traffic` POST endpoint. This script will send a observation and then wait for 10 seconds and send another one. All of this requires Python.

### 4. Use Postman to query the API
While the script is running you can install Postman and which should help us query the API. You can import the [Postman Collection](../api/flight_blender_api.postman_collection.json) prior. You will also need a "NoAuth" Bearer JWT token that you can generate by using the [get_access_token.py](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/get_access_token.py) script. You should have a scope of `blender.read` and a audience of `testflight.flightblender.com`. We will use this token to go to the Postman collection > Flight Feed Operations > Get airtraffic observations. You should be able to see output of the flight feed as a response!


## Frequently asked Questions (FAQs)

**Q: Docker compose errors out because of Postgres not launching**
A: Check existing Postgres port and / or shut down Postgres if you have it, Argon Server Docker uses the default SQL ports.

**Q: Where do I point my tools for Remote ID / Strategic Deconfliction APIs ?**
A: Check the [API Specification](http://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/openutm/flight-blender/master/api/flight-blender-1.0.0-resolved.yaml) to see the appropriate endpoints and / or download the [Postman Collection](../api/flight_blender_api.postman_collection.json) to see the endpoints.

**Q: Is there guide on how to configure Flight Passport can be configured to be used with Argon Server + Spotlight?**
A: Yes there is a small [OAUTH Infrastructure](oauth_infrastructure.md) document.
