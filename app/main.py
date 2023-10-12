import logging,os,traceback,sys, threading, configparser
from logging.handlers import TimedRotatingFileHandler
from flask import Flask,render_template,send_file,Blueprint,request
from api import api_bp
from backend import backend_thread
from database import checkdb

#Logging
global logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s.%(msecs)03d %(name)-14s %(levelname)-12s msg=%(message)s","%Y-%m-%d %H:%M:%S")

#StreamHandler
streamHandler = logging.StreamHandler()
streamHandler.setFormatter(formatter)
logger.addHandler(streamHandler)

#File
logfile = os.path.abspath(os.curdir)+"VaultTube.log"
timedHandler = TimedRotatingFileHandler(logfile,when="d",interval=1,backupCount=7)
logger.addHandler(timedHandler)

#Exception Handling
def log_uncaught_exceptions(ex_cls,ex,tb):
    logger.critical(''.join(traceback.format_tb(tb)))
    logger.critical('{0}: {1}'.format(ex_cls,ex))
    logger.critical('END PROCESS')
    logger.handlers = []

sys.excepthook = log_uncaught_exceptions

#Main
if __name__ == "__main__":
    #Load Config Data
    config = configparser.ConfigParser()
    config.read('config.ini')

    #Check Database
    checkdb(config,logger)


    #Flask Startup
    app = Flask(__name__)
    app.debug = True

    #Video static
    videos = Blueprint('videos',__name__,static_url_path='/videos',static_folder="/vault/Media/VaultTube")

    #Register with Flask
    app.register_blueprint(videos)
    app.register_blueprint(api_bp,url_prefix='/api')

    #Basic Routes
    @app.route('/')
    def home():
        return render_template('/index.html')

    #Start Threads
    be = threading.Thread(target=backend_thread,args=(logger,))
    be.start()

    #Begin
    logger.info("Starting VaultTube")
    app.run(host='0.0.0.0')