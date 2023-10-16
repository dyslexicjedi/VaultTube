import logging,os,traceback,sys, threading, configparser
from logging.handlers import TimedRotatingFileHandler
from flask import Flask,render_template,send_file,Blueprint,request
from api import api_bp
from backend import backend_thread
from database import checkdb
from scanner import start_scanner

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

#Load Config Data
config = configparser.ConfigParser()
config.read('config.ini')

#Flask Startup
app = Flask(__name__)
app.debug = True
app.config['dbconfig'] = config._sections['Database']
app.config['VaultDIR'] = config['Vault']['DIR']

#Video static
videos = Blueprint('videos',__name__,static_url_path='/videos',static_folder=config['Vault']['DIR'])

#Register with Flask
app.register_blueprint(videos)
app.register_blueprint(api_bp,url_prefix='/api')

#Basic Routes
@app.route('/')
def home():
    return render_template('/index.html')

@app.route('/player.html')
def player():
    return render_template('/player.html')

@app.route("/download.html")
def download():
    return render_template('/download.html')

@app.route("/channels.html")
def channels():
    return render_template("/channels.html")

@app.route("/creator.html")
def creator():
    return render_template("/creator.html")

@app.route("/search.html")
def search():
    return render_template("/search.html")

def startup():
    #Check Database
    dbpass = checkdb(config,logger)
    if(dbpass):
        #Start Threads
        be = threading.Thread(target=backend_thread,args=(config,logger))
        be.start()
        sc = threading.Thread(target=start_scanner,args=(config,logger))
        sc.start()
        #Begin
        logger.info("Starting VaultTube")
        app.run(host='0.0.0.0',use_reloader=False)
    else:
        logger.error("DB failed to start correctly, exiting")
        exit()

#Main
if __name__ == "__main__":
    startup()



    
  
