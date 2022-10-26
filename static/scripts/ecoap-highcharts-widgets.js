//https://github.com/highcharts/highcharts/issues/4544 - not called series.generatePoints() for a hidden series. 
$(function () {
    (function (H) {
        H.wrap(H.Series.prototype, 'setData', function (p) {
            p.apply(this, Array.prototype.slice.call(arguments, 1));
            this.processData();
            this.generatePoints();
            this.isDirty = true;
            this.isDirtyData = true;
            //console.log('wrap setdata')
            // optional redraw:
            if(arguments[2]) { this.chart.redraw(); }
        });
    })(Highcharts)
});


//================
//LINE CHART
//================
function addTwoYAxisLineChart(divId, data, xAxisType, xAxisTitle, yAxisTitle, y2AxisTitle){
	if (xAxisType == 'datetime'){ xAxis = {type: 'datetime', dateTimeLabelFormats: { day: '%Y-%m-%d' } } }
	else { xAxis = {type: 'linear', title: { text: xAxisTitle }} }
	return Highcharts.chart(divId, {
		chart: { backgroundColor: 'transparent', animation: false, /*zoomType: 'x'*/ },
	  	title: { text:'' }, subTitle: { text:'' },
		xAxis: xAxis,
		yAxis: [
			{ title: { text: yAxisTitle }, min: 0, max:100 }, 
			{ title: { text: y2AxisTitle, style: { color: '#aa6600' } }, opposite: true, min: 10, max: 35 }
		],
		//tooltip: { valueDecimals: 2 },
		legend: { enabled: false },
		plotOptions: {
		  series: { 
		  	pointStart: 0,
		  	point: {
                events: {
                    drag: function (e) { 
                    	return isOnAdvancedMode() //return true or false from advancedMode, charts editable if advancedmode is true
                	},
                    drop: function () {
                    	if(isOnAdvancedMode()){
	                    	//on drop, change the value on the table, according to the kind profile, typology and weekday
	                    	var divIdSplit = divId.split("_chart");//typologyDiv0,DU
	                    	var kind = this.series.userOptions.id //kind
	                    	var h = this.x; var v = this.y;
	                    	if (kind == 'heating'){
								$('#advanced'+kind+'Profile'+divIdSplit[0]+'_'+divIdSplit[1]+'_'+h)
								.text(v>15&&v<30? v : 'OFF')
								.css('color', v>15&&v<30? '#00bb00':'#bb0000')
			        		}
			        		else if (kind == 'cooling'){
			        			$('#advanced'+kind+'Profile'+divIdSplit[0]+'_'+divIdSplit[1]+'_'+h)
			        			.text(v>15&&v<30? v : 'OFF')
			        			.css('color', v>15&&v<30? '#00bb00':'#bb0000')
			        		}
			        		else{
			        			$('#advanced'+kind+'Profile'+divIdSplit[0]+'_'+divIdSplit[1]+'_'+h).text(v)
			        		}
	                    	//trigger the onchange to change other values if needed
	                    	var id = divIdSplit[0].split('Div')[1];//typology,0 - fetch the last digit
	                    	onChangeProfileValue(kind,id,divIdSplit[1],h)
	                    }
                    }
                }
            }
          },
          line: {
	        cursor: 'ns-resize'
	      }
		},
		series: data
	});
}
