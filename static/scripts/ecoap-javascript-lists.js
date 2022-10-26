var listOfClimateZones = {}
$.ajax({ type: "GET", url: '/ecoap/data/nuts-counties.json', async: false,
     success : function(response){
   		console.log('listOfClimateZones loaded /ecoap/data/nuts-counties.json')
   		listOfClimateZones = $.parseJSON(JSON.stringify(response));
     },
     error: function(response){
		console.log('listOfClimateZones error! ')
     }
});

var listOfRegulamentar = {}
$.ajax({ type: "GET", url: '/ecoap/data/regulamentar-values.json', async: false,
     success : function(response){
 		console.log('listOfRegulamentar loaded /ecoap/data/regulamentar-values.json')
 		listOfRegulamentar = $.parseJSON(JSON.stringify(response));
     },
     error: function(response){
		console.log('listOfRegulamentar error! ')
     }
});

var listOfDefinedValues = {}
$.ajax({ type: "GET", url: '/ecoap/data/default-values.json', async: false,
     success : function(response){
 		console.log('listOfDefinedValues loaded /ecoap/data/default-values.json')
 		listOfDefinedValues = $.parseJSON(JSON.stringify(response));
     },
     error: function(response){
		console.log('listOfDefinedValues error! ')
     }
});

//list of hourly profiles by typology
var listOfProfilesByTypology = {}
$.ajax({ type: "GET", url: '/ecoap/data/profile-values.json', async: false,
     success : function(response){
 		console.log('listOfProfilesByTypology loaded /ecoap/data/profile-values.json')
 		listOfProfilesByTypology = $.parseJSON(JSON.stringify(response));
     },
     error: function(response){
		console.log('listOfProfilesByTypology error! ')
     }
});



//Categoria de uso de elevadores por tipologia e respetiva categoria de uso
//tipologia+tipo de area = obter categoria de uso
var quadro33 = listOfDefinedValues['quadro33']	

//Distância média de viagem de elevadores (sm)
var quadro34 = listOfDefinedValues['quadro34']

//Coeficiente de manobra (A) e de inatividade e standby (B) depende da classe energetica do ascensor
var quadro35 = listOfDefinedValues['quadro35']

//Coeficiente de Desempenho Energético de escadas e tapetes (CDE)
var quadro36 = listOfDefinedValues['quadro36']

//Consumo de energia de escadas e tapetes em vazio (Pvazio)
var quadro37 = listOfDefinedValues['quadro37'] 

//Requisitos de qualidade térmica regulamentares das coberturas (U em W/(m2.ºC))

//Tipologia: Envolvente: Coberturas
var quadro39 = listOfDefinedValues['quadro39']

//Coeficiente de absorção da radiação solar (alfa) de acordo com a cor
var quadro311 = listOfDefinedValues['quadro311']

var quadro313 = listOfDefinedValues['quadro313']

var quadro316 = listOfDefinedValues['quadro316']

var quadro321 = listOfDefinedValues['quadro321']

var quadro322 = listOfDefinedValues['quadro322']

var quadro323 = listOfDefinedValues['quadro323']

var quadro324 = listOfRegulamentar['window']

var quadro325 = listOfRegulamentar['window-solar-factor']

var quadro327 = listOfRegulamentar['lights']

var quadro329 = listOfDefinedValues['quadro329']

var quadro332_333_334_335 = listOfDefinedValues['quadro332_333_334_335']

var quadroSolarTermico = listOfDefinedValues['solar-termico']

var quadroSolarFotovoltaico = listOfDefinedValues['solar-fotovoltaico']


//list of defaults by predominanttypologyId, buildingAreakind and yearId
//listOfDefaults[predominantTypology_id OR typology_id][areakind_id][yearid]	
var listOfDefaults = {}
$.ajax({ type: "GET", url: '/ecoap/data/typology-default-values.json', async: false,
     success : function(response){
 		console.log('listOfDefaults loaded /ecoap/data/typology-default-values.json')
 		listOfDefaults = $.parseJSON(JSON.stringify(response));
     },
     error: function(response){
		console.log('listOfDefaults error! ')
     }
});