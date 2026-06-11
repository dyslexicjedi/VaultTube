class QueueObject:
    def __init__(self, url, channel_id="", source="youtube", dl_progress=0, dl_status="", unsave=False):
        self.url = url
        self.channel_id = channel_id
        self.dl_progress = dl_progress
        self.dl_status = dl_status
        self.source = source
        self.unsave = unsave
        self.row_id = None   # queue table row backing this item, if persisted
        self.attempts = 0

    def update_dl_progress(self, progress):
        self.dl_progress = progress

    def get_dl_progress(self):
        return self.dl_progress
    
    def update_dl_active(self, active):
        self.dl_status = active
        
    def get_dl_active(self):
        return self.dl_status
    
    def set_source(self, source):
        self.source = source

    def get_source(self):
        return self.source  