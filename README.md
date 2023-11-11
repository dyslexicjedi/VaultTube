[![Dev Build](https://github.com/dyslexicjedi/VaultTube/actions/workflows/docker.yml/badge.svg?branch=dev)](https://github.com/dyslexicjedi/VaultTube/actions/workflows/docker.yml)
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
    environment:
      VAULTTUBE_VAULTDIR: <path to videos, generally will be /videos>
      VAULTTUBE_DBUSER: <database user>
      VAULTTUBE_DBPASS: <database password>
      VAULTTUBE_DBPORT: <database port>
      VAULTTUBE_DBHOST: <database ip>
      VAULTTUBE_DBNAME: <database name>
      VAULTTUBE_YTKEY: <YTKEY>
      VAULTTUBE_YTCOOKIE: <location to cookies.txt file>
    ports:
      - 5000:5000
    volumes:
      - <path to videos>:/videos
    restart: unless-stopped
    depends_on:
      - db
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

VaultTube only cares about the folder and file name, specifically the folder must have the ID of the Channel and the file must be the id of the video. Example:

`/UC_aabbbcccdddeeefffgggg/abcdefghijk.mkv`

This means you can easily import already downloaded content by placing it in the folder structure and waiting. The periodic scan will pick it up and add it to the database.

## Issues

**Q:** It's broken?
**A:** yes and?
##

**Q:** Why was this created?
**A:** I wanted an archiving solution that worked for my needs, others didn't fit what I wanted so I rolled my own. 
##

**Q:** I want *this*?
**A:** Open an issue and I'll see what I can do. This is a hobby project for my own archiving but I'll try to accommodate 