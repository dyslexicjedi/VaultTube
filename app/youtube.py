from pytube import YouTube
import time,os

complete = False

def processComplete(stream,fpath):
    print("Complete: "+fpath)
    global complete
    complete=True

def processing(stream,chunk,bytes_remaining):
    total_size = stream.filesize
    bytes_downloaded = total_size - bytes_remaining
    pct_completed = bytes_downloaded / total_size * 100
    print(f"Status: {round(pct_completed, 2)} %")

def single_download(url,logger,vaultdir):
    try:
        global complete
        logger.debug("Starting Download: %s"%url)
        yt = YouTube(url,on_complete_callback=processComplete,on_progress_callback=processing)
        if(not os.path.exists(vaultdir+"/"+yt.vid_info['videoDetails']['channelId'])):
            os.mkdir(vaultdir+"/"+yt.vid_info['videoDetails']['channelId'])
        ys = yt.streams.filter(progressive=True,file_extension='mp4').order_by('resolution').desc().first().download(filename=yt.vid_info['videoDetails']['videoId']+".mp4",output_path=vaultdir+"/"+yt.vid_info['videoDetails']['channelId']+"/")
        while not complete:
            time.sleep(5)
        complete = False
        return "True"
    except Exception as e:
        logger.error("YT Single Download Failed: %s"%e)