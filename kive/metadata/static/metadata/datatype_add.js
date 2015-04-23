
$(document).ready(function(){ // wait for page to finish loading before executing jQuery code
    // trigger ajax on CR drop-down to populate revision select
    $(document).ajaxSend(function(event, xhr, settings) {
        /*
            from https://docs.djangoproject.com/en/1.3/ref/contrib/csrf/#csrf-ajax
            On each XMLHttpRequest, set a custom X-CSRFToken header to the value of the CSRF token.
            ajaxSend is a function to be executed before an Ajax request is sent.
        */
        //console.log('ajaxSend triggered');

        function getCookie(name) {
            var cookieValue = null;
            if (document.cookie && document.cookie != '') {
                var cookies = document.cookie.split(';');
                for (var i = 0; i < cookies.length; i++) {
                    var cookie = jQuery.trim(cookies[i]);
                    // Does this cookie string begin with the name we want?
                    if (cookie.substring(0, name.length + 1) == (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }
        function sameOrigin(url) {
            // url could be relative or scheme relative or absolute
            var host = document.location.host; // host + port
            var protocol = document.location.protocol;
            var sr_origin = '//' + host;
            var origin = protocol + sr_origin;
            // Allow absolute or scheme relative URLs to same origin
            return (url == origin || url.slice(0, origin.length + 1) == origin + '/') ||
                (url == sr_origin || url.slice(0, sr_origin.length + 1) == sr_origin + '/') ||
                // or any other URL that isn't scheme relative or absolute i.e relative.
                !(/^(\/\/|http:|https:).*/.test(url));
        }
        function safeMethod(method) {
            return (/^(GET|HEAD|OPTIONS|TRACE)$/.test(method));
        }

        if (!safeMethod(settings.type) && sameOrigin(settings.url)) {
            xhr.setRequestHeader("X-CSRFToken", getCookie('csrftoken'));
        }
    });

    /*
    $("#id_Python_type").on('change', function() {
        if (this.value == 'str') {
            $("#int_constraints").hide();
            $("#str_constraints").show();
        }
        else if (this.value == 'int' || this.value == 'float') {
            $("#int_constraints").show();
            $("#str_constraints").hide();
        }
        else {
            $("#int_constraints").show();
            $("#str_constraints").show();
        }
    }
    ).change(); // trigger on load
    */

    // Ajax transaction to get Python type
    $('#id_to_hide, #int_constraints').hide();
    
    $('#id_restricts').on('change',
        function() {
            selected_options = $(this).val();

            if (selected_options.length > 0) {
                $.ajax({
                    type: "POST",
                    url: "get_python_type/",
                    data: { restricts: selected_options }, // specify data as an object
                    datatype: "json", // type of data expected back from server
                    success: function(result) {
                        var arr = JSON.parse(result)

                        if (arr.length > 1) {
                            // reject this combination of restrictions
                            $('#str_constraints, #int_constraints').hide(300); // animated with delay 300 ms
                            $('#bad_restrictions').text('Incompatible restriction of Datatypes');
                        } else {
                            $('#bad_restrictions').text('');
                            python_type = arr[0].fields.name;

                            if (python_type == 'integer' || python_type == 'float') {
                                $('#int_constraints').show(300);
                                $('#str_constraints').hide(300);
                            } else {
                                $('#int_constraints').hide(300);
                                $('#str_constraints').show(300);
                                
                                $('#id_minlen, #id_maxlen').prop('disabled', 'boolean' == python_type);// disabled if python type == 'boolean', otherwise enabled
                            }
                            
                            $('#id_to_hide').select(python_type);
                        }
                    }
                })
            }
        }
    ).change();

    // Pack help text into an unobtrusive icon
    $('.helptext', 'form').each(function() {
        $(this).wrapInner('<span class="fulltext"></span>').prepend('<a rel="ctrl">?</a>');
    });
    
    $('a[rel="ctrl"]').on('click', function (e) {
        $(this).siblings('.fulltext').show().css({ top: e.pageY, left: e.pageX, 'z-index': 3 });
        setTimeout("$('.fulltext').fadeOut(300);", 2000);
    });

});
