# Selfhosting
Selfhosting this bot means you MUST use the same license and make it Open Sourced.

# **Warning**
No help will be provided by any team member of OpenRobot and OpenRobot-Packages. You are all alone selfhosting this bot.

# Info
Selfhosting this bot will be hard. We already have the bot ready to [invite to your server](https://discord.com/api/oauth2/authorize?client_id=769026548560560178&permissions=8&scope=bot%20applications.commands). But nonetheless, here are what you will need to selfhost this bot:

1. Python3.10+ installed (with pip)
1. Git installed
1. PostgreSQL (14+ is recommended)
1. OpenRobot API Token
1. Perspective API Token

# Steps
1. Clone this repo 
```sh
$ git clone https://github.com/OpenRobot/Pixel-AI
```
2. Create a `config.yaml` file in the root folder of Pixel-AI witht this template:
```yaml
# The config for Pixel AI
# Perspective API is a free API to identify toxic comments that you can apply for. Find more info in https://perspectiveapi.com/
# AWS is a Cloud Provider with multiple APIs that can be used. https://aws.amazon.com/

bot:
  token: Insert-Your-Bot-Token
  prefix: Insert-Your-Prefix
  slash-commands: true # true/false. Defaults to true
  main-color: Insert-The-Hex-Code-Main-Color # such as #05AFD8 to 0x05AFD8
  description: Insert-Your-Bot-Description
  extensions: 
  - cogs.sentiment

authentication:
  # Tokens:
  aws: 
    # For AWS, you need to provide the 3 following permissions for IAM user.
    # - AmazonTranscribeFullAccess (Speech to text on sentiment)
    # - AmazonRekognitionFullAccess (NSFW Check)
    # - AmazonS3FullAccess (S3 upload for modlogs and mistakes)
    #
    # Optionally, you can make the value of `aws` key to null e.g 
    # aws: null
    # if you have your AWS cridentials on `~/.aws` directory.
    
    region: ...
    id: ...
    secret: ...

  perspective: Insert-Your-Perspective-Token # https://perspectiveapi.com/

  sentiment:
    enable: true # true/false.

    content: true # checks sentiment on message content in on_message.
    audio: true # checks sentiment in an audio file sent by a user
    ignore-bots: true # ignore messages sent by bots

  nsfw-check:
    enable: true # true/false.

    ignore-bots: true # ignore messages sent by bots

  ocr:
    enable: true # true/false.

database:
  psql: Insert-Your-PostgreSQL-Connection-String # a.k.a DSN.
  # e.g postgres://user:password@host:port/database
``` 
3. Create the required psql tables using this schema: 
```postgresql
CREATE TABLE sentiment(
    guild_id INTEGER PRIMARY KEY,
    modlog_channel INTEGER,
    is_enabled BOOLEAN DEFAULT true,
    users_ignored INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
    roles_ignored INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
    channels_ignored INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[]
);

CREATE TABLE nsfwcheck(
    guild_id INTEGER PRIMARY KEY,
    modlog_channel INTEGER,
    is_enabled BOOLEAN DEFAULT true,
    users_ignored INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
    roles_ignored INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
    channels_ignored INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[]
);

CREATE TABLE user_warnings(
    case_id INTEGER,
    guild_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER,
    case_type TEXT,
    case_time TIMESTAMP,
    case_description TEXT DEFAULT '',
    case_method TEXT,
    case_sources JSON,
    cdn_urls TEXT[],
    modlog_message_url TEXT,
    PRIMARY KEY (case_id, guild_id, channel_id, user_id, case_type)
);
```
4. Make a venv
```sh
$ python3 -m venv venv
```
5. Activate the venv    
    - Mac/Linux:
    ```sh
    $ source venv/bin/activate
    ```
    - Windows:
    ```sh
    $ .\venv\Scripts\activate
    ```
6. Install requirements:
```sh
$ pip install -r requirements.txt
```
7. Run the bot:
```sh
$ python3 bot.py
```