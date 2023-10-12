import glob,time,os

def backend_thread(logger):
    while 1:
        logger.info("Scanning Vault")
        for filename in glob.iglob('/vault/Media/VaultTube/**/*', recursive=True):
            print(os.path.abspath(filename), os.stat(filename).st_uid)
        time.sleep(60)