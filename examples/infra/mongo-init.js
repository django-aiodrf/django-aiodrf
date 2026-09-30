// Initialize only this example's single-node replica set; never replace a config.
try {
  rs.status();
} catch (error) {
  if (error.code !== 94) throw error; // NotYetInitialized
  rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongodb-db:27017"}]});
}
let ready = false;
for (let attempt = 0; attempt < 60; attempt++) {
  if (db.hello().isWritablePrimary) {
    ready = true;
    break;
  }
  sleep(1000);
}
if (!ready) throw new Error("Example MongoDB replica set did not elect a primary");
