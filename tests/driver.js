//
// Test driver file for mongos testing
//
//
// Properties to test:
//
// - Query correctness
// - Query performance
// - Metadata update aggressiveness
//
// under:
//
// - Different drivers
// - Random vs SemiRandom vs Incremental Shard Keys
// - Low vs Medium vs High Migration frequency
// - Low vs Medium vs High Op Rate
//    - Queries
//    - Inserts
//    - Updates
//    - Deletes
// - Replica set reconfiguration
// - Transient network failures
//    - To Shard
//    - To Config Servers
// - Unsharded (-> Sharded) -> Removal -> Unsharded -> (Sharded ->) ...
// - _id vs non-id vs multiple field shard key
// - Big vs Small query ranges
// - Few vs Many query ranges
// - Long-running vs medium-running vs short-running cursors
// - Big vs medium vs small documents
// - Long-duration vs medium-duration vs short-duration cursor batches
// - Sorted vs unsorted cursors
//

//
// The core idea here is a collection of documents that allow for tunable queries.
//
// The collection contains documents which represent numbers from -2^31->2^31-1
// { value : <value> }
// It also contains fields with randomized versions of the number over different random ranges,
// from +=1 to +=2^31 (totally random)
// { mix2^0 : <num +- 1>, mix2^1 : <num +- 2>, etc... }
// Over and underflow is handled by wrapping the values.
//
// Each document is approximately 0.5k (without extra data), allowing for a full collection size of
// 0.5KB * (2^32) = 2TB maximum (with no numeric duplicates).  By skipping every (2^N - 1) documents
// we can reduce the collection size by 1/(2^N).
//
// All documents have numeric values in the range (2^31)->(2^31-1)
// 
//
// We always fill the collection in-order, lowest->highest.
//
// The choice of shard index (over mix2^N) determines the randomness of this insert - where higher 
// N is more random.
//
// We always query the collection for ranges of exact values. The choice of shard key, 
// if one exists, determines the randomness of the shard choice we get.  We have to always include
// the shard key, and +- 2^n of the range if the shard key range is randomized.
//
// The "sparsity" of a query is a factor of the fill sparsity S_f / the query sparsity Q_s, 
// determined by the $mod operator over the exact value field: "31" : { $mod : [ 2^S_q, 0 ] }
//

// 
// Steps to setup a randomized query test environment
//
// 1) Choose the index randomness R_i, from mix2^0 to mix2^R_i
// 2) Choose the shard key randomness R_s, from mix2^0 to mix2^R_s
// 
// Given these parameters, we can then execute queries of different types:
// 
// 1) The range of the query Q_r, larger range equals more data returned
// 2) The sparsity of the query, Q_s, larger sparsity equals more data managed
//
// Since we start at zero, we can avoid wrap-around until collections become larger.  If our
// collection size is < 500GB and the max mix factor is 2^30, then we can avoid wraparound
// altogether.
// 

// Pre-calculate field names
var mixFieldNames = [ ];
for ( var i = 0; i < 32; i++) {
    mixFieldNames[i] = "mix2^" + i;
}

var twoTo32 = Math.pow(2, 32)
var twoTo31 = Math.pow(2, 31)

var wrap = function(value)
{
    var wrapVal = ((parseInt(value) + twoTo31) % twoTo32);
    if (wrapVal < 0)
        wrapVal += twoTo32;
    return wrapVal - twoTo31;
}

var smearOver = function(value, powerOf2Range)
{
    var range = Math.pow(2, powerOf2Range);
    value = value - Math.floor(range / 2) + Math.floor(Math.random() * range);
    return wrap(value);
}

var getDocument = function(clientId, value)
{
    value = wrap(value);

    var doc = {};
    doc.value = value;

    for ( var i = 0; i < 32; i++) {
        doc[mixFieldNames[i]] = smearOver(value, i + 1);
    }

    doc._id = ObjectId();
    doc.clientId = ObjectId();
    return doc;
}

var toRange = function(min, max)
{
    if (min.length)
        return min;

    if (min > max) {
        var swap = max;
        max = min;
        min = swap;
    }

    return [min,
            max];
}

var rangeSize = function(range)
{
    range = toRange(range);
    return range[1] - range[0];
}

var getQuery = function(clientId, shardKeyMix, indexMix, range, sparsity)
{
    range = toRange(range);
    var sparsityClause = {$mod : [Math.pow(2, sparsity),
                                  0]};

    var rangeClause = sparsityClause;
    rangeClause["$gte"] = range[0];
    rangeClause["$lt"] = range[1];

    query = {}
    query.clientId = clientId;
    query.value = rangeClause;

    {
        var mixAmount = Math.pow(2, shardKeyMix);

        // TODO: Make this better handle wraparound, doesn't currently.
        var mixRangeClause = {};
        mixRangeClause["$gte"] = range[0] - mixAmount;
        mixRangeClause["$lt"] = range[1] + mixAmount;

        query[mixFieldNames[shardKeyMix]] = mixRangeClause;
    }

    {
        var mixAmount = Math.pow(2, indexMix);

        // TODO: Make this better handle wraparound, doesn't currently.
        var mixRangeClause = {};
        mixRangeClause["$gte"] = range[0] - mixAmount;
        mixRangeClause["$lt"] = range[1] + mixAmount;

        query[mixFieldNames[indexMix]] = mixRangeClause;
    }

    return query
}

function PerfTest(coll, shardKeyMix, indexMix, queryDist, opPercents)
{

    if (!queryDist) {
        queryDist =
                    function(maxRange)
                    {
                        maxRange = toRange(maxRange);
                        var maxRangeSize = 300;

                        if (rangeSize(maxRange) < maxRangeSize)
                            return maxRange;

                        newRangeStart =
                                        Math
                                                .floor(Random.rand()
                                                       * (maxRange[1] - maxRangeSize));

                        return [newRangeStart,
                                newRangeStart + 300];

                    }
    }

    if (!opPercents) {
        opPercents = {query : 40,
                      insert : 20,
                      update : 20,
                      "delete" : 20}
    }

    var clientId = new ObjectId();
    var maxQueryRange = [0,
                         0];

    var maxUpdateRange = [0,
                          0];

    var maxDeleteRange = [0,
                          0];

    ops =
          {query : function()
           {
               var range = queryDist(maxRange);
               var sparse = 1;
               var query =
                           getQuery(clientId,
                                    shardKeyMix,
                                    indexMix,
                                    range,
                                    sparse);

               var results = coll.find(query).sort({value : 1});

               var nextValue = range[0];
               var numResults =
                                Math.ceil(rangeSize(range)
                                          / Math.pow(2, sparse));
               var resultsFound = 0;

               while (results.hasNext()) {
                   result = results.next();
                   resultsFound++;
                   assert.eq(result.value, nextValue);
                   nextValue += Math.pow(2, sparse);
               }

               assert.eq(resultsFound, numResults);
           },
           insert : function()
           {
               var doc = getDocument(clientId, maxRange[1]);
               coll.insert(doc);
               maxRange[1]++;
           },
           update : function()
           {
               
           },
           "delete" : function()
           {

           }}

    while (true) {

    }
}

PerfTest(5, 4)
