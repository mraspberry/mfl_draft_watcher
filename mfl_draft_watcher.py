#!/usr/bin/env python3

import collections
import configparser
import fasteners
import functools
import json
import logging
import logging.handlers
import mfl
import operator
import os
import pprint
import requests
import sys
import tempfile
from argparse import ArgumentParser
from datetime import date,datetime,timedelta

_MFL = mfl.API()
_LOCKFILE = os.path.join(tempfile.gettempdir(),'.mfl_draft_watcher')

def make_file_directory(*args):
    for filename in args:
        dirname = os.path.dirname(filename)
        try:
            os.makedirs(dirname)
        except OSError:
            pass

def fetch_players():
    # players ends up being a list of dicts
    players = _MFL.players()['players']['player']
    by_id = index_by_id(players)
    return by_id

def index_by_id(listobj):
    results = dict()
    for item in listobj:
        id = item['id']
        results[id] = item
    return results

def get_or_fetch(cachefile, fetch_func, func_args):
    if old(cachefile):
        logging.debug('Fetching new data for %s',cachefile)
        results = fetch_func(*func_args)
        with open(cachefile,'w',encoding='utf8') as cache:
            json.dump(results,cache)
    else:
        with open(cachefile,encoding='utf8') as cache:
            results = json.load(cache)

    return results

def cache_league_res(results,leaguecache):
    logging.debug('Caching full league results to %s',leaguecache)
    with open(leaguecache,'w',encoding='utf8') as league_fh:
        json.dump(results,league_fh)

def fetch_teams(leaguecache):
    json_res = _MFL.league()
    cache_league_res(json_res,leaguecache)
    franchises = json_res['league']['franchises']['franchise']
    return index_by_id(franchises)

def post_to_gm(msg,botid):
    data = {'bot_id': botid, 'text': msg}
    gm_url = 'https://api.groupme.com/v3/bots/post'
    result = requests.post(gm_url,data=data)
    result.raise_for_status()
    logging.debug('Result of GM post: %i',result.status_code)

def old(filename):
   logging.debug('Checking age of %s',filename)
   try:
        mtime = datetime.fromtimestamp(os.path.getmtime(filename))
   except OSError:
        logging.debug('%s not there. Making it old',filename)
        mtime = datetime.min # make it really old

   now = datetime.now()
   thres = timedelta(hours=24)
   diff = now - mtime
   logging.debug("Diff is %s",diff)
   return diff > thres

def load_prev_draft_info(filename):
    logging.info('Loading previous draft picks from %s',filename)
    with open(filename,'r',encoding='utf8') as draft_fh:
        info = json.load(draft_fh)
    return info

def get_draft_info(draftcache):
    try:
        prev_info = load_prev_draft_info(draftcache)
    except IOError:
        prev_info = collections.OrderedDict()

    new_info = collections.OrderedDict()
    getter = operator.itemgetter('franchise','round','pick','player')
    picks = _MFL.draftResults()['draftResults']['draftUnit']['draftPick']

    # if x['timestamp'] is an empty string the pick hasn't been made yet
    for pick in filter(lambda x: x['timestamp'],picks):
        prevkey = '_'.join(getter(pick))
        if not prevkey in prev_info:
            logging.debug('%s not a key in prev_info',prevkey)
            logging.debug(pprint.pformat(pick))
            new_info[prevkey] = pick

    # add in our new data
    prev_info.update(new_info)

    return (prev_info,new_info)

def setup_logger(logfile,loglevel):
    logger = logging.getLogger()
    logger.setLevel(loglevel)
    make_file_directory(logfile)
    handler = logging.handlers.TimedRotatingFileHandler(logfile,when='midnight',backupCount=14)
    formatter = logging.Formatter('{asctime}|{levelname:<8}|{name:<40}|{message}',style='{')
    handler.formatter = formatter
    logger.addHandler(handler)
    return logger

def check_draft(leagueid, playercache, leaguecache, teamcache, draftcache, botid):
    _MFL.leagueid = leagueid
    make_file_directory(playercache,leaguecache,teamcache,draftcache)
    teams = get_or_fetch(teamcache,fetch_teams,[leaguecache])
    num_teams = len(teams)
    players = get_or_fetch(playercache,fetch_players,list())
    (full_draft,new_picks) = get_draft_info(draftcache)
    msglist = list()
    template = "{rnd}.{pick}: {player}, {pos}, {team} - {opick} overall"
    for pickinfo in new_picks:
        (franchiseid, roundnum, picknum, playerid) = pickinfo.split('_')
        opick = (int(roundnum) - 1) * num_teams + int(picknum)
        if playerid == '----':
            playerinfo = dict(name='Skipped', position='N/A')
        else:
            playerinfo = players[playerid]
        teaminfo = teams[franchiseid]
        msgline = template.format(
            rnd=roundnum,
            pick=picknum,
            player=playerinfo['name'],
            pos=playerinfo['position'],
            team=teaminfo['name'],
            opick=opick,
            )
        msglist.append(msgline)
        logging.debug("Added '%s' to msglist",msgline)
    if msglist:
        msg = '\n'.join(msglist)
        post_to_gm(msg,botid)
        logging.info('Saving new draft picks in %s',draftcache)
        with open(draftcache,'w',encoding='utf8') as draft_fh:
            json.dump(full_draft,draft_fh)
    else:
        logging.info('No new picks made')

def filepath_getter(basefunc,value):
    return os.path.expandvars(basefunc(value))

@fasteners.interprocess_locked(_LOCKFILE)
def main():
    desc = 'Watches for draft picks to be made in MFL leagues and posts to GroupMe when they are'
    parser = ArgumentParser(description=desc)
    parser.add_argument('configfile',help="Configuration file containing league and GroupMe information")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    main_sect = 'draft_watcher'
    with open(args.configfile) as cf:
        config.readfp(cf)
    logfile = os.path.expandvars(config.get(main_sect,'logfile'))
    loglevel = getattr(logging,config.get(main_sect,'loglevel'),logging.DEBUG)
    logger = setup_logger(logfile,loglevel)
    logger.info('Starting up with PID %i',os.getpid())
    for sect in filter(lambda x:x != main_sect,config.sections()):
        base_getter = functools.partial(config.get,sect)
        fgetter = functools.partial(filepath_getter,base_getter)
        try:
            leagueid = config.getint(sect,'leagueid')
            playercache = fgetter('player_cache')
            leaguecache = fgetter('leagueinfo_cache')
            teamcache = fgetter('franchise_cache')
            draftcache = fgetter('draft_cache')
            botid = config.get(sect,'botid')
        except configparser.Error:
            logging.exception('Missing configuration values')
            continue
        try:
            check_draft(leagueid,playercache,leaguecache,teamcache,draftcache,botid)
        except Exception:
            logging.exception('Caught unhandled exception')
            raise
    logger.info('Run complete')

if __name__ == '__main__':
    main()
