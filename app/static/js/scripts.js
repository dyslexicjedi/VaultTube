function processdata(data){
    $("#carddeck").empty();
    $.each(data,function(i,item){
        var jobj = jQuery.parseJSON(data[i]["json"]);
        var txt = "<div class=\"col\"><div class=\"card h-100\" onclick=\"playvid('"+data[i].id+"')\"><img src=\"/api/images/"+data[i].id+"\" class=\"card-img-top img-fluid\" alt=\"...\"><div class=\"card-body\">";
        if(data[i].watched == 0){
            txt += "<div class=\"toprow\"><img src=\"/static/imgs/square.svg\" onclick=\"handlewatch(\'"+data[i].id+"\');\" />";
        }
        else{
            txt += "<div class=\"toprow\"><img src=\"/static/imgs/check-square.svg\" onclick=\"handlewatch(\'"+data[i].id+"\');\" />";
        }
        txt += "<h5 class=\"card-title\">"+jobj["items"][0]["snippet"]["title"]+"</h5></div>";
        txt += "<p><a href='/creator.html?creator="+data[i]['channelId']+"'>"+data[i]['youtuber']+"</a></p>";
        var d = data[i].PublishedAt.split(' ')[0]
        txt += "</div><div class=\"card-footer\"><small class=\"text-muted\">Published: "+d+"</small></br>";
        txt += "<small class=\"text-muted\">Length: "+data[i]['length'];
        txt += "</small></div></div></div>";
        $("#carddeck").append(txt);
    });
}
function pagination(id){
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
function playvid(id){
    window.location.href='/player.html?id='+id;
}
function subscribe(channelid){
    $.getJSON("/api/subscribe/channel/"+channelid,function(data){
        //To DO
    });
}
function unsubscribe(channelid){
    $.getJSON("/api/unsubscribe/channel/"+channelid,function(data){
        //To DO
    });
}
function subscribe_status(channelId){
    $.getJSON("/api/sub_status/channel/"+creator,function(data){
        if(data == 0){
            $("#subimg").attr('src','/static/imgs/square.svg');
        }
        else{
            $("#subimg").attr('src','/static/imgs/check-square.svg');
        }
        $("#subimg").attr('onclick','handlesub(\''+channelId+'\');');
    });
}
function handlesub(channelId){
    $.getJSON("/api/sub_status/channel/"+creator,function(data){
        if(data == 0){
            subscribe(channelId);
        }
        else{
            unsubscribe(channelId);
        }
    });
}
function watch_status(vid){
    $.getJSON("/api/watch_status/"+vid,function(data){
        if(data == 0){
            $("#watchstatus").attr('src','/static/imgs/square.svg');
        }
        else{
            $("#watchstatus").attr('src','/static/imgs/check-square.svg');
        }
        $("#watchstatus").attr('onclick','handlewatch(\''+vid+'\');');
    });
}
function handlewatch(vid){
    $.getJSON("/api/watch_status/"+vid,function(data){
        if(data == 0){
            $.ajax({url: "/api/watched/"+vid, success: function(result){
                $("#watchstatus").attr('src','/static/imgs/check-square.svg');
            }});
            watch_status(vid);
            contentChange();
        }
        else{
            $.ajax({url: "/api/unwatched/"+vid, success: function(result){
                $("#watchstatus").attr('src','/static/imgs/square.svg');
            }});
            watch_status(vid);
            contentChange();
        }
    });
    event.stopPropagation();
}
function playlist_sub_status(playlistid){
    $.getJSON("/api/sub_status/playlist/"+playlistid,function(data){
        if(data == 0){
            $("#subimg").attr('src','/static/imgs/square.svg');
        }
        else{
            $("#subimg").attr('src','/static/imgs/check-square.svg');
        }
        $("#subimg").attr('onclick','handlesub(\''+playlist+'\');');
    });
}