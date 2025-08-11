function processdata(data){
    $("#carddeck").empty();
    $.each(data,function(i,item){
        var jobj = jQuery.parseJSON(data[i]["json"]);
        var txt = "<div class=\"col\"><div class=\"card h-100\" onclick=\"playvid('"+data[i].id+"')\"><img src=\"/api/images/"+data[i].id+"\" class=\"card-img-top img-fluid\" alt=\"...\" style=\"width: 320px; height: 180px; object-fit: cover;\"><div class=\"card-body\">";
        if(data[i].watched == 0){
            txt += "<div class=\"toprow\"><img src=\"/static/imgs/square.svg\" onclick=\"handlewatch(\'"+data[i].id+"\');\" />";
        }
        else{
            txt += "<div class=\"toprow\"><img src=\"/static/imgs/check-square.svg\" onclick=\"handlewatch(\'"+data[i].id+"\');\" />";
        }
        if(!jobj){
            txt += "<h5 class=\"card-title\">"+data[i].title+"</h5></div>";
        } else {
            txt += "<h5 class=\"card-title\">"+data[i].title+"</h5></div>";
        }
        txt += "<p><a href='/creator.html?creator="+data[i]['channelId']+"'>"+data[i]['youtuber']+"</a></p>";
        var d = data[i].PublishedAt.split(' ')[0]
        txt += "</div><div class=\"card-footer\"><small class=\"text-muted\">Published: "+d+"</small></br>";
        txt += "<small class=\"text-muted\">Length: "+data[i]['length'];
        txt += "</small></div></div></div>";
        $("#carddeck").append(txt);
    });
}
function pagination(current, total) {
    $("#pagelist").empty();
    var txt = "";

    function pageItem(page, label = page, active = false, disabled = false) {
        return `<li class="page-item${active ? ' active' : ''}${disabled ? ' disabled' : ''}">
            <a class="page-link" href="#" onclick="update(${page});return false;">${label}</a>
        </li>`;
    }

    // Previous
    txt += current == 1 ? pageItem(current - 1, "Previous", false, true) : pageItem(current - 1, "Previous");

    // Calculate start and end page for display (max 5 pages)
    var startPage = Math.max(1, current - 2);
    var endPage = Math.min(total, current + 2);

    // Adjust if less than 5 pages visible
    if (endPage - startPage < 4) {
        if (startPage == 1) {
            endPage = Math.min(total, startPage + 4);
        } else if (endPage == total) {
            startPage = Math.max(1, endPage - 4);
        }
    }

    if (startPage > 1) {
        txt += pageItem(1);
        if (startPage > 2) txt += '<li class="page-item disabled"><a class="page-link">...</a></li>';
    }

    for (var i = startPage; i <= endPage; i++) {
        txt += pageItem(i, i, i === current);
    }

    if (endPage < total) {
        if (endPage < total - 1) txt += '<li class="page-item disabled"><a class="page-link">...</a></li>';
        txt += pageItem(total);
    }

    // Next
    txt += current == total ? pageItem(current + 1, "Next", false, true) : pageItem(current + 1, "Next");

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