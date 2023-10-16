function processdata(data){
    $("#carddeck").empty();
    $.each(data,function(i,item){
        var jobj = jQuery.parseJSON(data[i]["json"]);
        var txt = "<div class=\"col\"><div class=\"card h-100\" onclick=\"playvid('"+data[i].id+"')\"><img src=\"/api/images/"+data[i].id+"\" class=\"card-img-top img-fluid\" alt=\"...\"><div class=\"card-body\"><h5 class=\"card-title\">"+jobj["items"][0]["snippet"]["title"]+"</h5>";
        txt += "<p><a href='/creator.html?creator="+data[i]['channelId']+"'>"+data[i]['youtuber']+"</a></p>";
        txt += "</div><div class=\"card-footer\"><small class=\"text-muted\">Published: "+data[i].PublishedAt+"</small></div></div></div>";
        $("#carddeck").append(txt);
    });
}
function pagination(id){
    //var video_count = get_video_count();
    // Find if Video Count is less than number of pages.
    //ToDO
    //Get Videos
    //get_videos(id-1,opt);
    //Set Pagination buttons
    $("#pagelist").empty();
    var txt = "";
    if(id == 1){
        txt += "<li class=\"page-item disabled\"><a class=\"page-link\" href=\"#\">Previous</a></li>";
    }
    else{
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id-1)+");\">Previous</a></li>";
    }
    if(id < 4){
        if(id == 1){
            txt += "<li class=\"page-item active\"><a class=\"page-link\" href=\"#\" onclick=\"update(1);\">1</a></li>";
        }
        else{
            txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update(1);\">1</a></li>";
        }
        if(id == 2){
            txt += "<li class=\"page-item active\"><a class=\"page-link\" href=\"#\" onclick=\"update(2);\">2</a></li>";
        }
        else{
            txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update(2);\">2</a></li>";
        }
        if(id == 3){
            txt += "<li class=\"page-item active\"><a class=\"page-link\" href=\"#\" onclick=\"update(3);\">3</a></li>";
        }
        else{
            txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update(3);\">3</a></li>";
        }
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update(4);\">4</a></li>";
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update(5);\">5</a></li>";
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id+1)+");\">Next</a></li>";
    }
    else{
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id-2)+");\">"+(id-2)+"</a></li>";
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id-1)+");\">"+(id-1)+"</a></li>";
        txt += "<li class=\"page-item active\"><a class=\"page-link\" href=\"#\" onclick=\"update("+id+");\">"+id+"</a></li>";
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id+1)+");\">"+(id+1)+"</a></li>";
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id+2)+");\">"+(id+2)+"</a></li>";
        txt += "<li class=\"page-item\"><a class=\"page-link\" href=\"#\" onclick=\"update("+(id+1)+");\">Next</a></li>";
    }
    $("#pagelist").append(txt);
}