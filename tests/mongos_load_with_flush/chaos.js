//
// Chaos inducer for mongo clusters
//

var jsTest = {}
jsTest.log = function(msg)
{
    print("\n\n****" + msg + "\n****\n")
}

jsTest.options = function()
{
    return {}
}

function ChaosTest(mongos, sleepTime, opPercents)
{

    var config = mongos.getDB("config")

    if (!opPercents) {
        opPercents = {flushRouterConfig : 100,
                      stepDown : 0}
    }

    ops =
          {flushRouterConfig : function(verbose)
           {
               var mongoses = config.mongos.find().toArray()

               printjson(mongoses);

               for ( var i = 0; i < mongoses.length; i++) {

                   if (verbose) {
                       print("Flushing config of mongos router "
                             + mongoses[i]._id);
                   }

                   var admin = new Mongo(mongoses[i]._id).getDB("admin");
                   admin.runCommand({flushRouterConfig : true});
               }
           },
           stepDown : function(verbose)
           {
               if (verbose) {
                   print("Stepping down master of shard " + opts.shard);
               }

               var shards = config.shards.find().toArray();
               var shard = null

               var choice = Math.floor(Random.rand() * shards.length);
               shard = new Mongo(shards[choice].host);

               var admin = shard.getDB("admin");

               // Notify everyone that the shard may go down
               var notifyId = new ObjectId();
               config.test.insert({_id : notifyId,
                                   what : "stepDown",
                                   at : new Date(),
                                   shard : shards[choice]._id});

               assert.eq(null, config.getLastError());

               try {
                   admin.runCommand({replSetStepDown : 50,
                                     force : true});
                   print("No exception thrown on stepdown???")
               } catch (e) {
                   print("Exception expected on stepdown:" + tojson(e))
               }

               print("Waiting to remove notification...")

               // 10s is our time down at max
               sleep(10 * 1000)

               // Remove notification of the shard going down
               config.test.remove({_id : notifyId});
               assert.eq(null, config.getLastError());

           }}

    var counts = {}
    var total = 0;

    while (true) {

        var choice = Math.floor(Random.rand() * 100);
        var choiceFloor = 0;

        for (key in opPercents) {
            var percent = opPercents[key];

            if (choice > choiceFloor + percent) {
                choiceFloor += percent;
                continue;
            }

            // printjson(coll.find().toArray());

            ops[key](true);
            counts[key] = (counts[key] == undefined ? 0 : counts[key] + 1);
            total++;
            break;
        }

        jsTest.log("Current stats at " + new Date() + ": " + total + " ops.");
        printjson(counts);

        sleep(sleepTime);
    }

}

// For testing
var isLocal = false

if (isLocal) {

    jsTest.log("STARTING CHAOS")

    inlineOptions = {waitFor : 1000,
                     sleepTime : 5 * 1000}
}

var waitFor = inlineOptions.waitFor;
var mongos = db.getMongo();
var sleepTime = inlineOptions.sleepTime;

var start = new Date().getTime();
while (waitFor > (new Date().getTime() - start)) {
    sleep(1000);
    print("Waiting for " + (waitFor - (new Date().getTime() - start))
          + "ms to start...");
}

try {

    ChaosTest(mongos, sleepTime)

} catch (e) {

    printjson(e);
    jsTest.log("ERROR!");

    if (isLocal) {
        while (true)
            sleep(1000);
    }
}

jsTest.log("DONE!")
