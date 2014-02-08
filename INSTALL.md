Installation Instructions
=========================

Prerequisites
-------------

**shipyard** is a set of Django applications.  As a result, it has the following requirements:

1. Python 2.x (version 2.7 or higher) - unfortunately we do not support Python 3.x.
2. Django (version 1.6 or higher)

Source code or binaries for Python can be obtained from the official website, [python.org](www.python.org).  Most *nix distributions (including OS X) come with some version of Python.  

Instructions for downloading and installing Django can be found at [djangoproject.com](https://www.djangoproject.com/download/).


Project structure
-----------------

The root directory of **shipyard** should contain the following subdirectories:
* `/doc`
* `/samplecode`
* `/shipyard`

`/shipyard` is the top-level directory for the Django package that contains the project subdirectory (that by convention has the same name as the project folder, 'shipyard'), as well as a number of application subdirectories.  From now on, we will assume that you are in this project directory; *i.e.*, all paths will be defined relative to this directory.


Settings
--------

Since **shipyard** is a Django project, the majority of the installation procedure follows the standard instructions for Django.  The first thing you need to do is to make a copy of `/shipyard/settings_default.py` called `settings.py` (remember, all paths are relative to `/shipyard` so we mean `/shipyard/shipyard/settings_default.py`).  This is a standard step in the installation of a Django project where you configure project settings.  Within the `DATABASES['default']` dictionary, modify the respective values to indicate the type, location, and access credentials of your database.  For example, if you are using sqlite3 as your database engine, you would enter `'sqlite3'` under the key `ENGINE`, and the absolute path to the sqlite3 database file under the key `NAME`.  Note that this file does not have to exist - it will be created later.

You may also wish to modify the `TIME_ZONE` setting to your region, although this localization is not strictly necessary.


Initialize database
-------------------

Next, you need to make a copy of `./nukeDB_default.expect` and call it `nukeDB.expect`.  You need to replace all text that is highlighted in square brackets, as follows:

* `[PATH TO YOUR DB]` - an absolute or relative path to your database file, if you are using sqlite3.  **WARNING:** This will overwrite an existing database at this path, so you will lose everything if you execute the `nukeDB.expect` script after having used **shipyard** for any length of time.  As a precaution, you (as system administrator) may consider changing the user permission settings on all `nukeDB.*` files.
* `[YOUR E-MAIL ADDRESS HERE]` - for creating an admin account with the utility that is packaged with the Django distribution (`django.contrib.admin`).  Generally, it is not necessary to use this admin interface but we leave it as an open possibility.  It is okay to leave this blank, *i.e.,* as an empty string followed by a carriage return `"\r"`.
* `[YOUR PASSWORD]` - similarly, this is also used to initialize an admin account for the Django admin interface.  Unless you are really keen to use the admin tool, it is fine to enter an empty string here: `"\r"`.
* `[YOUR PASSWORD AGAIN]` - obviously, this should match the previous entry.


Finally, execute this *expect* script using the bash script `./nukeDB.bash`.  (Note that in OS X, *expect* scripts tend to appear to hang; just give it some time and it will proceed through the rest of the automation.  We don't know why this happens.)  This is a simple wrapper that calls `nukeDB.expect` and then executes a Django function that will populate the new database with some initial data.

You are now ready to run a local Django webserver - you just need to type `python manage.py runserver` and navigate to `localhost:8000` in your web browser!





