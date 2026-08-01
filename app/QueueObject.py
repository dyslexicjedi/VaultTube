class QueueObject:
    def __init__(self, url, channel_id="", source="youtube", dl_progress=0,
                 dl_status="", unsave=False, priority=0, origin="ordinary",
                 rescue_session_id=None, target_item_id=None,
                 download_delay_seconds=None):
        self.url = url
        self.channel_id = channel_id
        self.dl_progress = dl_progress
        self.dl_status = dl_status
        self.source = source
        self.unsave = unsave
        self.row_id = None   # queue table row backing this item, if persisted
        self.attempts = 0
        self.priority = int(priority)
        self.origin = origin
        self.rescue_session_id = rescue_session_id
        self.target_item_id = target_item_id
        self.download_delay_seconds = download_delay_seconds

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
