#!/usr/bin/python3
""" Demonstrating Flask, using APScheduler. """

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from loanscan_io.endpoints import *
from loanscan_io.mongodb import *

from functools import lru_cache

def sense_history():
    download_history('issuances')
    download_history('agreements')

def sense_rates():
    mark_event('interest_rates')

def sense_volume():
    download_volume('supply-volume')
    download_volume('borrow-volume')
    download_volume('repayment-volume')
    #download_volume('outstanding-debt')

sched = BackgroundScheduler(daemon=True)
sched.add_job(sense_rates,'interval',minutes=60)
sched.add_job(sense_history,'interval',minutes=240)
sched.add_job(sense_volume,'interval',minutes=1440)
sched.start()

from flask import render_template, send_from_directory

app = Flask(__name__, template_folder='html', static_url_path='/static')

import pymongo

def term_minutes(term):
    parts = term.split(".")
    
    if len(parts) == 1:
        days = 0
        subparts = parts[0].split(":")
    else:
        days = int(parts[0])
        subparts = parts[1].split(":")

    hours = int(subparts[0])
    minutes = int(subparts[1])
        
    return days*1440 + hours*60 + minutes

@lru_cache(maxsize=256)
def yield_agreement_data(protocol,symbol):
    print("Getting agreements...")
    agreements = list(db.agreements.find({"$where": "this.maturityDate > this.creationTime"}).sort("maturityTime", pymongo.DESCENDING))

    print("Getting yields")
    yield_data = [(agreement["loanProtocol"], agreement["tokenSymbol"],datetime.strptime(agreement["creationTime"],date_format), agreement["interestRate"],
                   term_minutes(agreement["loanTerm"]), datetime.strptime(agreement["maturityDate"], date_format)) for agreement in agreements]

    return [y for y in yield_data if y[1] == symbol and y[0] == protocol]

def empty_cache():
    print("Clearing cache")
    yield_agreement_data.cache_clear()

sched.add_job(empty_cache,'interval',minutes=240)

@app.route("/yield_curve/<protocol>/<symbol>")
def yield_curve(protocol, symbol):
    yield_data = yield_agreement_data(protocol,symbol)

    print("Sorted yields .. ")
    print([y[2].strftime(date_format) for y in sorted(yield_data, key=lambda x: x[2])[0:5]])
    print("Getting deltas...")
    timespot_diffs = [timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2), timedelta(days=1), timedelta(days=7), timedelta(days=15), timedelta(days=21), timedelta(days=28), timedelta(days=30), timedelta(days=60), timedelta(days=90), timedelta(days=180), timedelta(days=360)]
    time_now = datetime.utcnow()

    yields = {}
    for d in timespot_diffs:
        yields[int(d.total_seconds())] = []

    print("Getting curves...")
    maturities = list(yields.keys())
    
    for ti, delta in enumerate(timespot_diffs[0:-1]):
        timespot = time_now - delta
        maturity = int(delta.total_seconds())
        next_timespot = time_now - timespot_diffs[ti+1]
        agreements_before = [y for y in yield_data if y[2] <= timespot and y[2] > next_timespot]
        age_sorted = sorted(agreements_before, key = lambda y: y[2], reverse=True)
        maturity_sorted = sorted(age_sorted, key=lambda y: y[4])
                
        for i, m in enumerate(maturities[0:-1]):
            curve_points = [y for y in maturity_sorted if m <= y[4] and y[4] < maturities[i+1]]
            if len(curve_points) == 0:
                yields[maturity].append(0)
                continue            
            
            same_maturity_dots = [y for y in curve_points if y == m]
            if len(same_maturity_dots) > 0:
                avg = 1.0*sum([c[3] for c in same_dots_dots]) / len(same_maturity_dots)            
                yields[maturity].append( avg*10000 )
            else:
                yields[maturity].append( curve_points[0][3]*10000)

    return render_template('home.html', curves=yields, maturity_days=maturities)

from datetime import *

@app.route("/rate_spread/<protocol>/<symbol>")
def rate_curve(protocol, symbol):
    # curve points
    # 30d, 2y, 10y, 30y AHEAD
    # if 30y == 3d => 15m, 6h, 1d, 3d AHEAD
    timespot_diffs = [timedelta(days=3), timedelta(days=1), timedelta(minutes=15), timedelta(hours=6)]
    time_now = datetime.utcnow()
    timespots = sorted([time_now - diff for diff in timespot_diffs])
    borrow_curve_dots = []
    supply_curve_dots = []
    for t in timespots:
        rates = list(db.interest_rates.find({
            "snapshotTime": {"$lt": t.strftime(date_format)}
        }).sort("snapshotTime", pymongo.DESCENDING).limit(1))

        for rate in rates:
            for interest_rate in rate['interest_rates']:
                if interest_rate['provider'] == protocol:

                    for borrow in interest_rate['borrow']:
                        if borrow['symbol'].upper() == symbol.upper():
                            borrow_curve_dots.append(borrow['rate']*10000)
                    for supply in interest_rate['supply']:
                        if supply['symbol'].upper() == symbol.upper():
                            supply_curve_dots.append(supply['rate']*10000)

    return render_template('rate_spread.html', borrow_dots=",".join(map(str,borrow_curve_dots)), supply_dots=",".join(map(str, supply_curve_dots)))

if __name__ == "__main__":
    app.run(debug=True)