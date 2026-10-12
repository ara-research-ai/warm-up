const connection = new Mongo(process.env.MONGODB_URI);
const now = new Date();
const accounts = connection.getDB('ara-whatsapp').whatsappaccounts.find({
  status: {$ne: 'removed'},
  $or: [
    {status:'warmup'}, {status:'cooldown'},
    {'sim_recovery.state':'cooldown'},
    {'sim_recovery.cooldown_until':{$gt:now}},
    {'warmup.state':{$in:['cooldown','ready_to_activate']}}
  ]
}, {name:1,phone_number:1,business_id:1,status:1,'warmup.state':1,'warmup.completed_days':1,'sim_recovery.state':1,'sim_recovery.cooldown_until':1})
  .limit(101).maxTimeMS(15000).toArray();
if (accounts.length > 100) throw new Error('Account query bound exceeded');
const businesses = connection.getDB('ara-user').businesses.find(
  {_id:{$in:accounts.map(account=>account.business_id)}},
  {name:1,client_status:1}
).limit(101).maxTimeMS(15000).toArray();
const active = new Map(businesses.filter(b=>b.client_status==='active').map(b=>[b._id.toString(),b]));
const rows = accounts.filter(a=>a.business_id&&active.has(a.business_id.toString())).map(a=>{
  const cooldown = a.status==='cooldown'||a.sim_recovery?.state==='cooldown'||a.warmup?.state==='cooldown'||a.sim_recovery?.cooldown_until>now;
  const group = cooldown?'cooldown':a.warmup?.state==='ready_to_activate'?'ready':'warmup';
  return {name:a.name, number:a.phone_number, group, day:group==='warmup'?(a.warmup?.completed_days??0)+1:null};
});
print(JSON.stringify({capturedAt:new Date().toISOString(),rows}));
