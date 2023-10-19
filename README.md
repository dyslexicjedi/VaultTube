# VaultTube

VaultTube is a video archive and player application written in Python/Flask/Bootstrap5/HTML5

## Status: 
VaultTube is current pre-alpha (hot code!), expect bugs, crashes and similar issues. Treat it like hot code, because it is, use at your own risk.

Who should use this: Alpha testers, people who don't mind "early-access" to help improve software.

## How-TO:
Below is a docker compose entry for the database and vaulttube

```
version: "3"
services:
  db:
    image: mariadb
    environment:
      MYSQL_ROOT_PASSWORD: SuperSecretPassword
      MYSQL_DATABASE: vaulttube
      MYSQL_USER: vaulttube
      MYSQL_PASSWORD: SuperSecretPassword
    volumes:
      - /docker/mariadb:/var/lib/mysql
    ports:
      - "3306:3306"
  vaulttube:
    image: dyslexicjedi/vaulttube:<dev or latest>
    container_name: vaulttube
    ports:
      - 5000:5000
    volumes:
      - <path to videos>:/videos
      - <path to config file>:/app/config.ini
    restart: unless-stopped
    depends_on:
      - db
```

## Config File
Below is an example config file

```
[Database]
host=<database IP>
database=vaulttube
user=vaulttube
password=SuperSecretPassword
autocommit=True

[YouTube]
KEY = <your youtube key>

[Vault]
DIR = /videos
```

## Info

Problem? Open an issue [Issues](https://github.com/jedihomelab/VaultTube/issues) 

## Legally: 
Software is provided as is, use at own risk.

The authors are not to be held responsible for misuse, reuse, recycled and unintended uses of content within this repository by others. Only archive content that you have legal rights to or is public domain. 

This is free software under the GPL 2.0 open source license.

### Tags: 
`latest` will be current stable build, `dev` will be experimental builds based on each commit (bleeding edge)

## Video File Name:

VaultTube only cares about the end of the file name, specifically the youtube id must be at the end of the file name with [ ] around it. Example:

`some crazy test file video[abcdefghijk].mkv`

This means you can easily import already downloaded content, not just from the download.sh script.

## Issues

**Q:** It's broken?
**A:** yes and?
##

**Q:** Why was this created?
**A:** I wanted an archiving solution that worked for my needs, others didn't fit what I wanted so I rolled my own. 
##

**Q:** I want *this*?
**A:** Open an issue and I'll see what I can do. This is a hobby project for my own archiving but I'll try to accommodate 