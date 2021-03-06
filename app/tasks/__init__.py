"""Tasks related to celery."""
from .. import mongo, logger
import celery
import datetime
import json
import os
import re
import requests
from pyasn import mrtx
import codecs
from urllib.request import urlopen

from ..utils.helpers import str_now_time


APP_BASE = os.path.dirname(os.path.realpath(__file__)).replace('/tasks', '')
ASNAMES_URL = 'http://www.cidr-report.org/as2.0/autnums.html'
HTML_FILENAME = "autnums.html"
EXTRACT_ASNAME_C = re.compile(r"<a .+>AS(?P<code>.+?)\s*</a>\s*(?P<name>.*)", re.U)


def __parse_asname_line(line):
    match = EXTRACT_ASNAME_C.match(line)
    return match.groups()


def _html_to_dict(data):
    """Translates an HTML string available at `ASNAMES_URL` into a dict."""
    split = data.split("\n")
    split = filter(lambda line: line.startswith("<a"), split)
    fn = __parse_asname_line
    return dict(map(fn, split))


def download_asnames():
    """Downloads and parses to utf-8 asnames html file."""
    http = urlopen(ASNAMES_URL)
    data = http.read()
    http.close()

    raw_data = data.decode('latin-1')
    raw_data = raw_data.encode('utf-8')
    return raw_data.decode("utf-8")


@celery.task(name="fetch-as-names")
def fetch_as_names():
    """Process the AS names."""
    data = download_asnames()
    data_dict = _html_to_dict(data)
    data_json = json.dumps(data_dict)
    output = '%s/resources/as_names.json' % APP_BASE
    with codecs.open(output, 'w', encoding="utf-8") as fs:
        fs.write(data_json)


def build_filename():
    """Build out the filename based on current UTC time."""
    now = datetime.datetime.utcnow()
    fname = now.strftime('rib.%Y%m%d.%H00.bz2')
    hour = int(now.strftime('%H'))
    if not hour % 2 == 0:
        if len(str(hour)) == 1:
            hour = "0%d" % (hour - 1)
        else:
            hour = hour - 1
        fname = now.strftime('rib.%Y%m%d.')
        fname = fname + str(hour) + '00.bz2'
    return fname


def gen_request():
    """Build the routeview URL to download."""
    base = "http://archive.routeviews.org//bgpdata/"
    now = datetime.datetime.utcnow()
    slug = now.strftime('%Y.%m')
    fname = build_filename()
    url = "%s/%s/RIBS/%s" % (base, slug, fname)
    return {'url': url, 'filename': fname}


def to_download():
    """Check to see if we need to download."""
    now = datetime.datetime.utcnow()
    fname = build_filename()
    config = json.load(open('%s/resources/config.json' % APP_BASE))
    if fname == config['file']:
        return False
    return True


@celery.task(name="fetch-rib")
def fetch_rib(force=False):
    """Process the routeview data."""
    if not to_download() and not force:
        return
    logger.debug("Downloading the latest RIB")
    meta = gen_request()
    response = requests.get(meta['url'])
    path = '%s/resources/ribs/%s' % (APP_BASE, meta['filename'])
    open(path, 'wb').write(response.content)
    logger.debug("RIB file saved")
    current = '%s/resources/current' % (APP_BASE)
    logger.debug("Converting RIB to database format")
    prefixes = mrtx.parse_mrt_file(path, print_progress=False,
                                   skip_record_on_error=True)
    mrtx.dump_prefixes_to_file(prefixes, current, path)
    logger.debug("Updated the database")
    config = {'file': meta['filename'], 'last_update': str_now_time()}
    json.dump(config, open('%s/resources/config.json' % APP_BASE, 'w'),
              indent=4)
